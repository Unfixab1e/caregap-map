"""Deterministic ICU evidence extraction.

Scans a facility record for ICU-related signals using the configurable
keyword groups in :mod:`caregap_map.config`. Every signal retains the exact
original text fragment that triggered it, so a reviewer can always trace a
score back to the source record.

Important: keyword matches are *signals of what the record claims*, never
proof of real clinical capability. Scoring and the UI both surface this.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from functools import lru_cache
from typing import Any

from pydantic import BaseModel, Field

from .cleaning import normalize_null_like, parse_int_safe, parse_list_field
from .config import EVIDENCE_TEXT_FIELDS, ScoringConfig

# Fragment length cap for long free-text matches.
_MAX_FRAGMENT_LEN = 300

# Groups that count as positive ICU evidence (negation is handled separately).
_POSITIVE_GROUPS = ("explicit_icu", "equipment", "procedure", "staffing")


class EvidenceFragment(BaseModel):
    """One keyword hit, traceable to its source field and exact text."""

    field: str  # facility column the text came from
    group: str  # signal group (explicit_icu, equipment, procedure, staffing, negation)
    pattern: str  # regex that matched
    text: str  # exact original fragment (list item or sentence window)


class EvidenceResult(BaseModel):
    """Structured outcome of evidence extraction (any extractor).

    ``extractor`` records provenance ("deterministic" or "llm");
    ``unclear_claims`` and ``extraction_explanation`` are only populated by
    the model-backed extractor.
    """

    explicit_icu_claim: bool = False
    icu_bed_count: int | None = None
    capacity_signal: bool = False
    supporting_fields: list[str] = Field(default_factory=list)
    equipment_signals: list[str] = Field(default_factory=list)
    procedure_signals: list[str] = Field(default_factory=list)
    staffing_signals: list[str] = Field(default_factory=list)
    supporting_text_fragments: list[EvidenceFragment] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    contradiction_flags: list[str] = Field(default_factory=list)
    suspicious_claim_flags: list[str] = Field(default_factory=list)
    extractor: str = "deterministic"
    unclear_claims: list[str] = Field(default_factory=list)
    extraction_explanation: str | None = None


@lru_cache(maxsize=32)
def _compile(patterns: tuple[str, ...]) -> tuple[re.Pattern, ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


def _sentence_window(text: str, start: int, end: int) -> str:
    """Extract the sentence (or a capped window) around a match span."""
    boundary = re.compile(r"[.;\n]")
    left = 0
    for m in boundary.finditer(text, 0, start):
        left = m.end()
    right_match = boundary.search(text, end)
    right = right_match.start() if right_match else len(text)
    fragment = text[left:right].strip()
    if len(fragment) > _MAX_FRAGMENT_LEN:
        center = max(start - left, 0)
        lo = max(center - _MAX_FRAGMENT_LEN // 2, 0)
        fragment = fragment[lo : lo + _MAX_FRAGMENT_LEN].strip()
    return fragment


def field_texts(record: Mapping[str, Any]) -> dict[str, list[str]]:
    """Collect searchable text items per evidence field.

    ``description`` is one free-text block; list fields become one item per
    list entry so fragments map 1:1 to original entries.
    """
    texts: dict[str, list[str]] = {}
    for field in EVIDENCE_TEXT_FIELDS:
        raw = record.get(field)
        if field == "description":
            value = normalize_null_like(raw)
            texts[field] = [value] if value else []
        else:
            texts[field] = parse_list_field(raw)
    return texts


def extract_evidence(record: Mapping[str, Any], config: ScoringConfig) -> EvidenceResult:
    """Run deterministic ICU evidence extraction over one facility record.

    ``record`` holds the raw field values (as read from facilities.csv).
    Returns an :class:`EvidenceResult`; every signal keeps the exact
    original fragment that produced it.
    """
    kw = config.keywords
    texts = field_texts(record)
    result = EvidenceResult()
    matched_patterns: dict[str, set[str]] = {g: set() for g in _POSITIVE_GROUPS}
    supporting_fields: set[str] = set()

    negation_patterns = _compile(tuple(kw.negation))
    group_patterns = {g: _compile(tuple(getattr(kw, g))) for g in _POSITIVE_GROUPS}

    for field, items in texts.items():
        for item in items:
            negated_here = False
            for pattern in negation_patterns:
                m = pattern.search(item)
                if m:
                    negated_here = True
                    result.supporting_text_fragments.append(
                        EvidenceFragment(
                            field=field,
                            group="negation",
                            pattern=pattern.pattern,
                            text=_sentence_window(item, m.start(), m.end()),
                        )
                    )
                    if "negated_icu_mention" not in result.contradiction_flags:
                        result.contradiction_flags.append("negated_icu_mention")
            if negated_here:
                # A text item that explicitly negates ICU capability must not
                # also contribute positive evidence from the same sentence.
                continue
            for group, patterns in group_patterns.items():
                for pattern in patterns:
                    m = pattern.search(item)
                    if m:
                        matched_patterns[group].add(pattern.pattern)
                        supporting_fields.add(field)
                        result.supporting_text_fragments.append(
                            EvidenceFragment(
                                field=field,
                                group=group,
                                pattern=pattern.pattern,
                                text=_sentence_window(item, m.start(), m.end()),
                            )
                        )

    # Different patterns can match the same text; keep one fragment per
    # (field, group, text) so the reviewer sees each source sentence once.
    seen: set[tuple[str, str, str]] = set()
    unique_fragments = []
    for frag in result.supporting_text_fragments:
        key = (frag.field, frag.group, frag.text)
        if key not in seen:
            seen.add(key)
            unique_fragments.append(frag)
    result.supporting_text_fragments = unique_fragments

    result.explicit_icu_claim = bool(matched_patterns["explicit_icu"])
    result.equipment_signals = sorted(matched_patterns["equipment"])
    result.procedure_signals = sorted(matched_patterns["procedure"])
    result.staffing_signals = sorted(matched_patterns["staffing"])
    result.supporting_fields = sorted(supporting_fields)

    # ICU bed counts (capacity signal).
    all_items = [item for items in texts.values() for item in items]
    result.icu_bed_count = extract_icu_bed_count(all_items, config)
    result.capacity_signal = result.icu_bed_count is not None

    apply_consistency_checks(record, result)
    result.missing_evidence = build_missing_evidence(texts, result)

    return result


def extract_icu_bed_count(texts: Iterable[str], config: ScoringConfig) -> int | None:
    """Extract an ICU bed count that is properly anchored in ONE text passage.

    The configured ``icu_bed_count`` patterns require the number, a bed word
    and ICU/intensive-care context to occur together - a number that merely
    co-occurs with ICU elsewhere ("10 ventilators; ICU available") never
    counts. Shared by the deterministic extractor and by the LLM extractor's
    verification step. Returns the largest anchored count, else ``None``.
    """
    patterns = _compile(tuple(config.keywords.icu_bed_count))
    counts: list[int] = []
    for item in texts:
        for pattern in patterns:
            for m in pattern.finditer(item):
                try:
                    counts.append(int(m.group(1)))
                except (ValueError, IndexError):
                    continue
    return max(counts) if counts else None


def apply_consistency_checks(record: Mapping[str, Any], result: EvidenceResult) -> None:
    """Suspicious-claim heuristics (dataset consistency, not clinical truth).

    Shared by every extractor implementation - deterministic and LLM-backed
    results go through the identical checks.
    """
    total_capacity = parse_int_safe(record.get("capacity"))
    if (
        result.icu_bed_count is not None
        and total_capacity is not None
        and total_capacity > 0
        and result.icu_bed_count > total_capacity
        and "icu_beds_exceed_total_capacity" not in result.suspicious_claim_flags
    ):
        result.suspicious_claim_flags.append("icu_beds_exceed_total_capacity")
    if (
        result.explicit_icu_claim
        and total_capacity == 0
        and "icu_claim_with_zero_capacity" not in result.suspicious_claim_flags
    ):
        result.suspicious_claim_flags.append("icu_claim_with_zero_capacity")


def build_missing_evidence(texts: dict[str, list[str]], result: EvidenceResult) -> list[str]:
    """What is missing to judge this record confidently? Extractor-agnostic."""
    missing: list[str] = []
    if not texts.get("description"):
        missing.append("no description text")
    if not result.explicit_icu_claim:
        missing.append("no explicit ICU / intensive-care claim")
    if not result.equipment_signals:
        missing.append("no ICU-relevant equipment mentioned")
    if not result.procedure_signals:
        missing.append("no ICU-relevant procedures mentioned")
    if not result.staffing_signals:
        missing.append("no critical-care staffing information")
    if result.icu_bed_count is None:
        missing.append("no ICU bed count")
    return missing
