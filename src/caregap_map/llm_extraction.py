"""Optional LLM-backed ICU evidence extraction.

Implements the same interface as the deterministic extractor
(:func:`caregap_map.evidence.extract_evidence`) and returns the same
:class:`EvidenceResult` model, so scoring, validation and classification
do not know or care which extractor produced the evidence.

Trust rules (non-negotiable):

- The model only *proposes* evidence. Every quoted fragment is verified to be
  an exact substring of the source record (whitespace-tolerant); fragments
  that cannot be located are dropped and counted in a suspicious flag.
- ``explicit_icu_claim`` is only honoured when backed by a verified fragment.
- The same deterministic consistency checks and missing-evidence rules run
  on the result, and the final score/classification still passes through
  the deterministic validator (see ``scoring.score_facility``).

The OpenAI API key comes from ``OPENAI_API_KEY``; nothing here stores it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from functools import lru_cache
from typing import Any, Protocol

from .config import LlmConfig, ScoringConfig
from .evidence import (
    EvidenceFragment,
    EvidenceResult,
    apply_consistency_checks,
    build_missing_evidence,
    detect_icu_subtypes,
    extract_icu_bed_count,
    field_texts,
)

_POSITIVE_GROUPS = ("explicit_icu", "equipment", "procedure", "staffing")
_VALID_GROUPS = set(_POSITIVE_GROUPS) | {"negation"}

SYSTEM_PROMPT = """\
You extract ICU (intensive care) capability evidence from Indian health-facility records.
You are a data-quality assistant, not a medical judge: report only what the text claims.

Rules:
1. Quote supporting text EXACTLY as written in the provided fields. Never paraphrase,
   translate, fix spelling, or merge sentences. A quote must be a contiguous excerpt.
2. Only quote text relevant to ICU / intensive care / critical care capability:
   explicit ICU claims, ICU-relevant equipment (ventilators, ECMO, monitors, BiPAP...),
   ICU-relevant procedures (mechanical ventilation, intubation, resuscitation...),
   critical-care staffing (intensivists, ICU nurses...).
3. If the text DENIES ICU capability (e.g. "has no ICU"), quote it with group "negation".
4. If one quote supports several groups (e.g. "NICU with ventilator support" is both an
   explicit ICU claim and equipment evidence), repeat the quote once per group.
5. Report an ICU bed count only if a number of ICU beds is stated in the text.
6. List claims that are too vague to categorise under unclear_claims.
7. Do not infer capability from the facility name, size, or reputation.
"""

# Response contract for OpenAI structured outputs (strict mode).
LLM_RESPONSE_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "explicit_icu_claim": {
            "type": "boolean",
            "description": "True only if the text explicitly claims ICU/intensive/critical care.",
        },
        "icu_bed_count": {
            "type": ["integer", "null"],
            "description": "Stated number of ICU beds, or null.",
        },
        "fragments": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "field": {
                        "type": "string",
                        "enum": ["description", "capability", "specialties", "procedure", "equipment"],
                    },
                    "group": {
                        "type": "string",
                        "enum": ["explicit_icu", "equipment", "procedure", "staffing", "negation"],
                    },
                    "quote": {"type": "string", "description": "Exact contiguous excerpt."},
                },
                "required": ["field", "group", "quote"],
            },
        },
        "unclear_claims": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Claims too vague to categorise.",
        },
        "explanation": {
            "type": "string",
            "description": "One short paragraph explaining the extraction.",
        },
    },
    "required": ["explicit_icu_claim", "icu_bed_count", "fragments", "unclear_claims", "explanation"],
}


class LlmExtractionError(RuntimeError):
    """The model response was unusable (transport error or invalid JSON)."""


class LlmClient(Protocol):
    """Minimal completion interface; lets tests inject a stub."""

    def complete_json(self, system: str, user: str, schema: dict, config: LlmConfig) -> str: ...


def estimate_cost_usd(prompt_tokens: int, completion_tokens: int, config: LlmConfig) -> float:
    """Rough USD cost from token counts and the configured per-1M prices."""
    return (
        prompt_tokens * config.input_cost_per_mtok + completion_tokens * config.output_cost_per_mtok
    ) / 1_000_000


class OpenAiClient:
    """Thin adapter over the OpenAI SDK implementing :class:`LlmClient`.

    Tracks cumulative token usage so callers can report and cap spend
    (``total_prompt_tokens`` / ``total_completion_tokens`` /
    :meth:`estimated_cost_usd`).
    """

    def __init__(self, api_key: str | None = None) -> None:
        try:
            import openai
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise ImportError(
                "The 'openai' package is required for LLM extraction. "
                'Install it with: pip install -e ".[llm]"'
            ) from exc
        # Falls back to the OPENAI_API_KEY environment variable.
        self._client = openai.OpenAI(api_key=api_key) if api_key else openai.OpenAI()
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0

    def estimated_cost_usd(self, config: LlmConfig) -> float:
        return estimate_cost_usd(self.total_prompt_tokens, self.total_completion_tokens, config)

    def complete_json(self, system: str, user: str, schema: dict, config: LlmConfig) -> str:
        response = self._client.chat.completions.create(
            model=config.model,
            temperature=config.temperature,
            max_tokens=config.max_output_tokens,
            timeout=config.request_timeout_s,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "icu_evidence", "schema": schema, "strict": True},
            },
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        usage = getattr(response, "usage", None)
        if usage is not None:
            self.total_prompt_tokens += usage.prompt_tokens or 0
            self.total_completion_tokens += usage.completion_tokens or 0
        content = response.choices[0].message.content
        if not content:
            raise LlmExtractionError("Model returned an empty response.")
        return content


def build_user_prompt(texts: dict[str, list[str]]) -> str:
    """Render the record's evidence fields for the model, one block per field."""
    blocks = []
    for field, items in texts.items():
        if not items:
            continue
        body = "\n".join(f"- {item}" for item in items)
        blocks.append(f"[{field}]\n{body}")
    return "Facility record fields:\n\n" + "\n\n".join(blocks)


def is_informative_fragment(text: str, config: ScoringConfig) -> bool:
    """Reject verified but semantically empty quotes (e.g. "True", "8").

    Substring verification alone cannot catch quotes that exist in the source
    yet carry no ICU meaning - observed on corrupted, column-shifted records.
    Multi-token quotes pass; a single token must match one of the configured
    ICU keyword patterns to count.
    """
    if len(text.split()) >= 2:
        return True
    kw = config.keywords
    patterns = _compile_keywords(tuple(kw.explicit_icu + kw.equipment + kw.procedure + kw.staffing))
    return any(p.search(text) for p in patterns)


@lru_cache(maxsize=8)
def _compile_keywords(patterns: tuple[str, ...]) -> tuple[re.Pattern, ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


def locate_fragment(quote: str, source: str) -> str | None:
    """Find ``quote`` in ``source`` tolerating whitespace differences.

    Returns the *source's own* exact substring (so traceability always points
    at original text), or ``None`` if the quote does not occur verbatim.
    """
    tokens = quote.split()
    if not tokens:
        return None
    pattern = r"\s+".join(re.escape(t) for t in tokens)
    match = re.search(pattern, source)
    return match.group(0) if match else None


class LlmEvidenceExtractor:
    """Model-backed evidence extractor with verified, source-anchored quotes."""

    def __init__(self, client: LlmClient, config: ScoringConfig | None = None) -> None:
        self._client = client
        self._config = config or ScoringConfig()

    def extract(self, record: Mapping[str, Any]) -> EvidenceResult:
        """Extract ICU evidence for one facility record via the LLM.

        Raises :class:`LlmExtractionError` when the model response cannot be
        parsed; callers decide whether to fall back to the deterministic
        extractor.
        """
        texts = field_texts(record)
        result = EvidenceResult(extractor="llm")

        if not any(texts.values()):
            result.missing_evidence = build_missing_evidence(texts, result)
            return result

        raw = self._client.complete_json(
            SYSTEM_PROMPT, build_user_prompt(texts), LLM_RESPONSE_SCHEMA, self._config.llm
        )
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LlmExtractionError(f"Model response is not valid JSON: {exc}") from exc

        matched_groups: dict[str, list[str]] = {g: [] for g in _POSITIVE_GROUPS}
        supporting_fields: set[str] = set()
        dropped = 0
        low_information = 0

        for frag in payload.get("fragments", []):
            field = frag.get("field")
            group = frag.get("group")
            quote = frag.get("quote", "")
            if group not in _VALID_GROUPS or field not in texts:
                dropped += 1
                continue
            located = next((loc for item in texts[field] if (loc := locate_fragment(quote, item))), None)
            if located is None:
                dropped += 1
                continue
            if not is_informative_fragment(located, self._config):
                low_information += 1
                continue
            result.supporting_text_fragments.append(
                EvidenceFragment(field=field, group=group, pattern="llm", text=located)
            )
            if group == "negation":
                if "negated_icu_mention" not in result.contradiction_flags:
                    result.contradiction_flags.append("negated_icu_mention")
            else:
                matched_groups[group].append(located[:80])
                supporting_fields.add(field)

        if dropped:
            # The model quoted text that does not exist in the record.
            result.suspicious_claim_flags.append(f"llm_unverified_fragments_dropped:{dropped}")
        if low_information:
            # Verified but meaningless quotes (e.g. "True" on corrupted rows).
            result.suspicious_claim_flags.append(f"llm_low_information_fragments_dropped:{low_information}")

        # A claim only counts when backed by a verified explicit fragment.
        result.explicit_icu_claim = bool(payload.get("explicit_icu_claim")) and bool(
            matched_groups["explicit_icu"]
        )
        result.equipment_signals = matched_groups["equipment"]
        result.procedure_signals = matched_groups["procedure"]
        result.staffing_signals = matched_groups["staffing"]
        result.supporting_fields = sorted(supporting_fields)

        # A bed count is believed only when it can be re-derived from ONE
        # verified fragment that contains the number together with bed and
        # ICU/intensive-care context (deterministic anchoring, shared with
        # the baseline extractor). A number that merely co-occurs with ICU
        # across fragments ("10 ventilators" + "ICU available") never counts.
        anchored = extract_icu_bed_count([f.text for f in result.supporting_text_fragments], self._config)
        payload_count = payload.get("icu_bed_count")
        if anchored is not None:
            result.icu_bed_count = anchored
            result.capacity_signal = True
            if isinstance(payload_count, int) and payload_count != anchored:
                result.suspicious_claim_flags.append("llm_bed_count_mismatch")
        elif isinstance(payload_count, int):
            result.suspicious_claim_flags.append("llm_bed_count_unanchored")

        # Subtype detection runs deterministically over the VERIFIED fragments,
        # so both extractors share identical subtype semantics.
        result.icu_subtypes = detect_icu_subtypes(result.supporting_text_fragments, self._config)

        result.unclear_claims = [str(c) for c in payload.get("unclear_claims", [])][:10]
        result.extraction_explanation = str(payload.get("explanation", "")) or None

        apply_consistency_checks(record, result)
        result.missing_evidence = build_missing_evidence(texts, result)
        return result
