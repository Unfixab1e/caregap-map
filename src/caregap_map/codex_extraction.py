"""Offline, resumable Codex CLI batch evidence extraction.

Uses ChatGPT-authenticated ``codex exec`` (no OPENAI_API_KEY) to propose ICU
evidence for facility batches. Codex only *proposes*: every quote is anchored
verbatim to the supplied record and the existing deterministic validators,
scorers and classifier decide everything (via
:func:`caregap_map.llm_extraction.payload_to_evidence` - the exact guardrails
of the OpenAI extractor). This is a preprocessing workflow; the Streamlit app
never calls Codex.

Codex runs read-only and ephemeral, receives only evidence-bearing fields on
stdin, and writes nothing but its schema-constrained final message (the
orchestrator owns all checkpointing). Every successful record is appended to
``completed.jsonl`` immediately; failures are quarantined in ``errors.jsonl``
without stopping the run; ``--resume`` continues after interruption.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .config import ScoringConfig
from .llm_extraction import payload_to_evidence
from .scoring import score_facility

PROMPT_VERSION = "codex-icu-v1"
SCHEMA_VERSION = "1"
DEFAULT_MODEL = "gpt-5.6-luna"
FALLBACK_MODEL = "gpt-5.6-terra"
DEFAULT_REASONING_EFFORT = "low"

# Only evidence-bearing fields go to the model - no name, geography, scores,
# classifications, URLs or social metrics (reduces anchoring bias and tokens).
PROMPT_INPUT_FIELDS = ("unique_id", "description", "capability", "specialties", "procedure", "equipment")

CODEX_TASK_INSTRUCTIONS = """\
You are an offline healthcare-facility evidence extractor.

The <stdin> block contains a JSON object: {"records": [...]} where each record
has unique_id plus the supplied source fields (description, capability,
specialties, procedure, equipment). Process every supplied facility
independently.

Your task is not to determine clinical truth and not to assign a final
classification. Extract only what the supplied source fields explicitly claim.

Rules:
1. Quote supporting text exactly as it appears in the supplied field.
2. Never paraphrase, translate, repair spelling, normalise wording, or combine
   separate excerpts.
3. Do not infer capability from the facility name, reputation, organisation
   type, location, size, or presumed services.
4. An explicit ICU claim requires explicit ICU, intensive-care, critical-care,
   or recognised ICU-subtype language. camelCase specialty tags (e.g.
   criticalCareMedicine) are classifier labels, not explicit claims.
5. A quote may be returned once for each applicable signal group. For example,
   "NICU with ventilator support" may produce both an explicit ICU fragment
   and an equipment fragment.
6. Report an ICU bed count only when one quoted excerpt clearly links the
   number to ICU beds.
7. Distinguish ICU subtypes: general_or_unspecified, cardiac_icu,
   neonatal_icu, pediatric_icu, medical_icu, surgical_icu.
8. Report negated claims (e.g. "has no ICU") under the negation group.
9. Put vague or promotional claims that cannot be judged under unclear_claims.
10. Process every requested unique_id exactly once; add no other ids.
11. Return only data conforming to the supplied JSON schema, as your final
    message. Do not run commands, read files, or produce any other output.

A downstream deterministic system verifies every quotation and decides all
scores and classifications.
"""


# ---------------------------------------------------------------------------
# Batch result models
# ---------------------------------------------------------------------------


class CodexFragment(BaseModel):
    model_config = {"extra": "forbid"}
    field: str
    group: str
    quote: str


class CodexRecordResult(BaseModel):
    model_config = {"extra": "forbid"}
    unique_id: str
    explicit_icu_claim: bool
    icu_bed_count: int | None
    icu_subtypes: list[str] = Field(default_factory=list, max_length=6)
    fragments: list[CodexFragment] = Field(default_factory=list, max_length=20)
    unclear_claims: list[str] = Field(default_factory=list, max_length=10)
    explanation: str = Field(default="", max_length=500)


class CodexBatchResult(BaseModel):
    model_config = {"extra": "forbid"}
    records: list[CodexRecordResult]


class CodexError(RuntimeError):
    """Batch-level Codex failure (retryable/splittable)."""


class CodexAuthError(CodexError):
    """Authentication or usage-limit failure - pause the run, do not churn."""


class CodexNotInstalledError(CodexError):
    """The codex executable is not available."""


# Legitimate unique_ids are UUID-like tokens. Corrupted column-shifted rows
# carry prose/emails/markdown in unique_id; the model cannot echo those
# faithfully, so each one drags its whole batch through the retry/split
# ladder. They are quarantined BEFORE any Codex call (they are unjudgeable
# source rows anyway).
UNIQUE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{5,63}$")


def partition_valid_ids(records: list[Mapping[str, Any]]) -> tuple[list, list]:
    """Split records into (codex-safe, corrupted-id) lists."""
    valid: list = []
    corrupted: list = []
    for rec in records:
        target = valid if UNIQUE_ID_PATTERN.match(str(rec.get("unique_id", ""))) else corrupted
        target.append(rec)
    return valid, corrupted


def parse_batch_result(raw: str, requested_ids: list[str]) -> CodexBatchResult:
    """Parse and strictly validate one batch output.

    Every requested id must appear exactly once; unknown ids are rejected.
    Raises :class:`CodexError` with a precise reason on any violation.
    """
    if not raw.strip():
        raise CodexError("empty model response")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CodexError(f"malformed JSON: {exc}") from exc
    try:
        result = CodexBatchResult.model_validate(payload)
    except Exception as exc:
        raise CodexError(f"schema validation failed: {exc}") from exc

    seen = [r.unique_id for r in result.records]
    duplicates = {i for i in seen if seen.count(i) > 1}
    if duplicates:
        raise CodexError(f"duplicate ids in response: {sorted(duplicates)}")
    unknown = set(seen) - set(requested_ids)
    if unknown:
        raise CodexError(f"unknown ids in response: {sorted(unknown)}")
    missing = set(requested_ids) - set(seen)
    if missing:
        raise CodexError(f"missing ids in response: {sorted(missing)}")
    return result


# ---------------------------------------------------------------------------
# Codex CLI invocation
# ---------------------------------------------------------------------------

_AUTH_MARKERS = ("not logged in", "login", "authentication", "unauthorized", "usage limit", "rate limit")


def build_codex_command(
    schema_path: Path,
    output_path: Path,
    model: str,
    reasoning_effort: str,
    working_dir: Path,
) -> list[str]:
    """The exact ``codex exec`` invocation (flags confirmed on codex-cli 0.144.x)."""
    return [
        "codex",
        "exec",
        "--ephemeral",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--cd",
        str(working_dir),
        "-m",
        model,
        "-c",
        f"model_reasoning_effort={reasoning_effort}",
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(output_path),
        "--color",
        "never",
        CODEX_TASK_INSTRUCTIONS,
    ]


class CodexRunner:
    """Runs one batch through ``codex exec``; ``exec_fn`` injectable for tests."""

    def __init__(self, exec_fn: Callable[..., subprocess.CompletedProcess] | None = None) -> None:
        self._exec_fn = exec_fn or self._default_exec

    @staticmethod
    def _default_exec(cmd: list[str], stdin_text: str, timeout: float) -> subprocess.CompletedProcess:
        # Resolve the executable explicitly: on Windows the npm-installed
        # Codex CLI is a .cmd shim that bare subprocess lookup cannot find.
        import shutil

        resolved = shutil.which(cmd[0])
        if resolved is None:
            raise FileNotFoundError(cmd[0])
        return subprocess.run(
            [resolved, *cmd[1:]],
            input=stdin_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
        )

    def run_batch(
        self,
        batch_input: dict,
        schema_path: Path,
        output_path: Path,
        model: str,
        reasoning_effort: str,
        timeout: float,
        working_dir: Path,
    ) -> str:
        """Execute one batch; returns the raw final-message text."""
        cmd = build_codex_command(schema_path, output_path, model, reasoning_effort, working_dir)
        stdin_text = json.dumps(batch_input, ensure_ascii=False)
        try:
            proc = self._exec_fn(cmd, stdin_text, timeout)
        except FileNotFoundError as exc:
            raise CodexNotInstalledError(
                "codex executable not found - install the Codex CLI and run `codex login`."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise CodexError(f"codex exec timed out after {timeout:.0f}s") from exc

        stderr_tail = (proc.stderr or "")[-2000:]
        combined = f"{proc.stdout or ''}\n{stderr_tail}".lower()
        if proc.returncode != 0:
            if any(marker in combined for marker in _AUTH_MARKERS):
                raise CodexAuthError(
                    f"codex exec failed (exit {proc.returncode}) with an authentication/usage-limit "
                    f"signal; run `codex login` or wait for the limit to reset, then resume with "
                    f"--resume. stderr tail: {stderr_tail[:300]!r}"
                )
            raise CodexError(f"codex exec exit {proc.returncode}; stderr tail: {stderr_tail[:300]!r}")
        if not output_path.exists():
            raise CodexError("codex exec produced no output file")
        return output_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Checkpoint store
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _atomic_write_json(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class CodexRunStore:
    """Owns the run directory: manifest, completed/error journals, batch files.

    All writes are atomic (tmp+rename) or append+flush, so an interrupted run
    never corrupts prior progress. JSONL journals skip torn trailing lines on
    load instead of failing the resume.
    """

    def __init__(self, output_dir: str | Path) -> None:
        self.root = Path(output_dir)
        self.batches_dir = self.root / "batches"
        self.manifest_path = self.root / "manifest.json"
        self.completed_path = self.root / "completed.jsonl"
        self.errors_path = self.root / "errors.jsonl"
        self.root.mkdir(parents=True, exist_ok=True)
        self.batches_dir.mkdir(parents=True, exist_ok=True)

    # -- manifest -----------------------------------------------------------

    def load_manifest(self) -> dict | None:
        if not self.manifest_path.exists():
            return None
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def save_manifest(self, manifest: dict) -> None:
        manifest["updated_at"] = _utcnow()
        _atomic_write_json(self.manifest_path, manifest)

    # -- journals -----------------------------------------------------------

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict]:
        if not path.exists():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # torn trailing line from an interrupted write
        return rows

    @staticmethod
    def _append_jsonl(path: Path, row: dict) -> None:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def append_completed(self, row: dict) -> None:
        self._append_jsonl(self.completed_path, row)

    def append_error(self, row: dict) -> None:
        self._append_jsonl(self.errors_path, row)

    def load_completed(self) -> list[dict]:
        return self._read_jsonl(self.completed_path)

    def completed_ids(self) -> set[str]:
        return {r["unique_id"] for r in self.load_completed()}

    # -- batch artefacts ----------------------------------------------------

    def write_batch_files(self, batch_id: str, batch_input: dict, meta: dict) -> Path:
        input_path = self.batches_dir / f"{batch_id}.input.json"
        _atomic_write_json(input_path, batch_input)
        _atomic_write_json(self.batches_dir / f"{batch_id}.meta.json", meta)
        return input_path

    def batch_output_path(self, batch_id: str) -> Path:
        return self.batches_dir / f"{batch_id}.output.json"

    def archive(self) -> Path | None:
        """Move an existing run aside (used by --restart); never deletes."""
        if not any(self.root.iterdir()):
            return None
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        target = self.root.with_name(f"{self.root.name}_archived_{stamp}")
        self.root.rename(target)
        self.root.mkdir(parents=True, exist_ok=True)
        self.batches_dir.mkdir(parents=True, exist_ok=True)
        return target


# ---------------------------------------------------------------------------
# Record conversion (reuses ALL existing guardrails + scoring)
# ---------------------------------------------------------------------------


def build_batch_input(records: list[Mapping[str, Any]]) -> dict:
    """Minimal per-facility payload for the model."""
    return {
        "records": [
            {field: (rec.get(field) if rec.get(field) is not None else None) for field in PROMPT_INPUT_FIELDS}
            for rec in records
        ]
    }


def score_codex_record(
    codex_record: CodexRecordResult,
    source_record: Mapping[str, Any],
    config: ScoringConfig,
) -> dict:
    """Verify, validate and score one Codex proposal with EXISTING code.

    The payload goes through :func:`payload_to_evidence` (fragment anchoring,
    low-information filter, bed-count anchoring, subtype detection, specialty
    reclassification, consistency checks) and then the standard
    :func:`score_facility` (deterministic validators, both scores, the
    four-state classification). No scoring logic lives in this module.
    """
    payload = codex_record.model_dump()
    evidence = payload_to_evidence(payload, source_record, config, extractor_name="codex_cli")
    scored = score_facility(source_record, config, extractor=lambda _record: evidence)

    dropped = low_info = 0
    for flag in evidence.suspicious_claim_flags:
        if flag.startswith("llm_unverified_fragments_dropped:"):
            dropped = int(flag.split(":")[1])
        if flag.startswith("llm_low_information_fragments_dropped:"):
            low_info = int(flag.split(":")[1])

    return {
        "unique_id": codex_record.unique_id,
        "extractor": "codex_cli",
        "classification": scored.classification,
        "classification_reason": scored.classification_reason,
        "capability_evidence_score": scored.capability_evidence_score,
        "data_completeness_score": scored.data_completeness_score,
        "explicit_icu_claim": evidence.explicit_icu_claim,
        "icu_bed_count": evidence.icu_bed_count,
        "icu_subtypes": evidence.icu_subtypes,
        "model_proposed_subtypes": codex_record.icu_subtypes,  # provenance only
        "verified_fragments": len(evidence.supporting_text_fragments),
        "dropped_fragments": dropped,
        "low_information_fragments": low_info,
        "fragments": [f.model_dump() for f in evidence.supporting_text_fragments],
        "validation_flags": [f.model_dump() for f in scored.validation_flags],
        "corroboration_categories": scored.corroboration_categories,
        "unclear_claims": evidence.unclear_claims,
        "explanation": evidence.extraction_explanation,
    }


# ---------------------------------------------------------------------------
# Batch processing with retry / split / quarantine
# ---------------------------------------------------------------------------


class BatchProcessor:
    """Processes record batches with retry-once, then split-in-half semantics."""

    def __init__(
        self,
        runner: CodexRunner,
        store: CodexRunStore,
        config: ScoringConfig,
        schema_path: Path,
        model: str,
        reasoning_effort: str,
        timeout: float,
        max_retries: int = 2,
    ) -> None:
        self.runner = runner
        self.store = store
        self.config = config
        self.schema_path = schema_path
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.timeout = timeout
        self.max_retries = max_retries
        self.batch_counter = 0
        self.retries = 0
        self.executions = 0

    def _next_batch_id(self) -> str:
        self.batch_counter += 1
        return f"batch_{self.batch_counter:06d}"

    def _run_once(self, records: list[Mapping[str, Any]], batch_id: str) -> list[dict]:
        requested_ids = [r["unique_id"] for r in records]
        batch_input = build_batch_input(records)
        input_hash = hashlib.sha256(json.dumps(batch_input, sort_keys=True).encode("utf-8")).hexdigest()[:16]
        meta = {
            "batch_id": batch_id,
            "requested_ids": requested_ids,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "prompt_version": PROMPT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "input_hash": input_hash,
            "started_at": _utcnow(),
        }
        self.store.write_batch_files(batch_id, batch_input, meta)
        output_path = self.store.batch_output_path(batch_id)

        self.executions += 1
        raw = self.runner.run_batch(
            batch_input,
            self.schema_path,
            output_path,
            self.model,
            self.reasoning_effort,
            self.timeout,
            working_dir=self.store.batches_dir,
        )
        result = parse_batch_result(raw, requested_ids)

        by_id = {r["unique_id"]: r for r in records}
        rows = []
        for codex_record in result.records:
            row = score_codex_record(codex_record, by_id[codex_record.unique_id], self.config)
            row.update(
                {
                    "model": self.model,
                    "reasoning_effort": self.reasoning_effort,
                    "prompt_version": PROMPT_VERSION,
                    "schema_version": SCHEMA_VERSION,
                    "batch_id": batch_id,
                    "input_hash": input_hash,
                    "processed_at": _utcnow(),
                }
            )
            rows.append(row)
        return rows

    def process(self, records: list[Mapping[str, Any]]) -> tuple[int, int]:
        """Process one batch; returns (succeeded, failed) record counts.

        Failure ladder: retry the same batch once; then split it in half and
        recurse; a persistently failing single record is quarantined to
        errors.jsonl. Auth/usage-limit errors propagate to pause the run.
        """
        last_error: CodexError | None = None
        for _attempt in range(self.max_retries):
            batch_id = self._next_batch_id()
            try:
                rows = self._run_once(records, batch_id)
            except CodexAuthError:
                raise
            except CodexError as exc:
                last_error = exc
                self.retries += 1
                continue
            for row in rows:
                self.store.append_completed(row)
            return len(rows), 0

        if len(records) > 1:
            mid = len(records) // 2
            ok1, fail1 = self.process(records[:mid])
            ok2, fail2 = self.process(records[mid:])
            return ok1 + ok2, fail1 + fail2

        self.store.append_error(
            {
                "unique_id": records[0]["unique_id"],
                "error": str(last_error),
                "model": self.model,
                "prompt_version": PROMPT_VERSION,
                "failed_at": _utcnow(),
            }
        )
        return 0, 1


# ---------------------------------------------------------------------------
# Run-level helpers
# ---------------------------------------------------------------------------


def new_manifest(
    *,
    source_path: str,
    source_hash: str,
    model: str,
    reasoning_effort: str,
    batch_size: int,
    timeout: float,
    requested: int,
    codex_version: str,
    repo_commit: str,
) -> dict:
    return {
        "run_id": uuid.uuid4().hex,
        "extractor": "codex_cli",
        "model": model,
        "reasoning_effort": reasoning_effort,
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "source_path": source_path,
        "source_sha256": source_hash,
        "requested_records": requested,
        "succeeded_records": 0,
        "failed_records": 0,
        "completed_batches": [],
        "batch_size": batch_size,
        "timeout_s": timeout,
        "codex_cli_version": codex_version,
        "repo_commit": repo_commit,
        "started_at": _utcnow(),
        "verification": {"verified_fragments": 0, "dropped_fragments": 0, "low_information_fragments": 0},
    }


def check_resume_compatible(manifest: dict, *, model: str, source_hash: str) -> list[str]:
    """Mismatches that make a resume unsafe (empty list = safe)."""
    problems = []
    if manifest.get("model") != model:
        problems.append(f"model changed: {manifest.get('model')} -> {model}")
    if manifest.get("prompt_version") != PROMPT_VERSION:
        problems.append(f"prompt version changed: {manifest.get('prompt_version')} -> {PROMPT_VERSION}")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"schema version changed: {manifest.get('schema_version')} -> {SCHEMA_VERSION}")
    if manifest.get("source_sha256") != source_hash:
        problems.append("source file hash changed since the run started")
    return problems


def codex_cli_version() -> str:
    import shutil

    resolved = shutil.which("codex")
    if resolved is None:
        return "unavailable"
    try:
        proc = subprocess.run([resolved, "--version"], capture_output=True, text=True, timeout=30)
        return (proc.stdout or proc.stderr).strip()
    except Exception:
        return "unavailable"


def repo_commit_sha() -> str:
    try:
        proc = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=30)
        return proc.stdout.strip()
    except Exception:
        return "unavailable"


def elapsed_since(start: float) -> float:
    return time.monotonic() - start
