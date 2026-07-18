"""Scoring and classification.

Two *independent* scores per facility:

- ``capability_evidence_score`` (0-100): how strongly the record supports
  that the facility has ICU capability.
- ``data_completeness_score`` (0-100): whether there is enough information
  to judge the facility at all.

Classification combines them without ever conflating "no evidence" with
"no ICU": a record that cannot be judged is *Insufficient Data*, never a gap.
All weights and thresholds come from :class:`caregap_map.config.ScoringConfig`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field

from .cleaning import normalize_null_like, parse_coordinates, parse_int_safe, parse_list_field
from .config import (
    CLASS_INSUFFICIENT,
    CLASS_LIKELY_GAP,
    CLASS_NEEDS_REVIEW,
    CLASS_TRUSTED,
    ScoringConfig,
)
from .evidence import EvidenceResult, extract_evidence
from .validator import (
    SEV_CONTRADICTION,
    SEV_SUSPICIOUS,
    ValidationFlag,
    has_severity,
    validate_facility,
)


class FacilityScore(BaseModel):
    """Full scoring outcome for one facility, traceable to fragments."""

    capability_evidence_score: int
    data_completeness_score: int
    classification: str
    classification_reason: str
    evidence: EvidenceResult
    validation_flags: list[ValidationFlag] = Field(default_factory=list)
    evidence_components: dict[str, int] = Field(default_factory=dict)
    completeness_components: dict[str, int] = Field(default_factory=dict)

    @property
    def contradiction_flags(self) -> list[str]:
        return [f.name for f in self.validation_flags if f.severity == SEV_CONTRADICTION]


def compute_evidence_score(
    evidence: EvidenceResult, config: ScoringConfig
) -> tuple[int, dict[str, int]]:
    """Score how strongly the record supports ICU capability (0-100)."""
    w = config.evidence_weights
    components: dict[str, int] = {}
    if evidence.explicit_icu_claim:
        components["explicit_claim"] = w.explicit_claim
    if evidence.equipment_signals:
        components["equipment"] = w.equipment
    if evidence.procedure_signals:
        components["procedure"] = w.procedure
    if evidence.icu_bed_count is not None:
        components["capacity"] = w.capacity
    if evidence.staffing_signals:
        components["staffing"] = w.staffing
    if evidence.explicit_icu_claim and len(evidence.supporting_fields) >= 3:
        components["multi_field_bonus"] = w.multi_field_bonus
    if evidence.contradiction_flags:
        components["negation_penalty"] = -w.negation_penalty
    if evidence.suspicious_claim_flags:
        components["suspicious_penalty"] = -w.suspicious_penalty
    score = max(0, min(100, sum(components.values())))
    return score, components


def compute_completeness_score(
    record: Mapping[str, Any], config: ScoringConfig
) -> tuple[int, dict[str, int]]:
    """Score whether the record holds enough information to be judged (0-100).

    Deliberately independent of *what* the record claims - only whether the
    evidence-bearing fields are populated and the geography is usable.
    """
    w = config.completeness_weights
    components: dict[str, int] = {}
    if normalize_null_like(record.get("description")) is not None:
        components["description"] = w.description
    if parse_list_field(record.get("procedure")):
        components["procedure"] = w.procedure
    if parse_list_field(record.get("equipment")):
        components["equipment"] = w.equipment
    if parse_int_safe(record.get("numberDoctors")) is not None:
        components["staffing"] = w.staffing
    if parse_int_safe(record.get("capacity")) is not None:
        components["capacity"] = w.capacity
    if parse_list_field(record.get("source_urls")):
        components["source_url"] = w.source_url

    coord_status = record.get("coord_status")
    if coord_status is None:
        _, _, coord_status = parse_coordinates(record.get("latitude"), record.get("longitude"))
    geo_usable = coord_status == "ok" or normalize_null_like(record.get("state_final")) is not None
    if geo_usable:
        components["geography"] = w.geography

    score = max(0, min(100, sum(components.values())))
    return score, components


def classify(
    evidence_score: int,
    completeness_score: int,
    has_contradiction: bool,
    has_suspicious: bool,
    config: ScoringConfig,
) -> tuple[str, str]:
    """Map the two scores plus validator outcomes to one of the four classes.

    Order of precedence (documented in DECISIONS.md):
    contradictions first, then judgeability, then evidence strength.
    """
    t = config.thresholds
    if has_contradiction:
        return CLASS_NEEDS_REVIEW, "Record contradicts itself about ICU capability."
    if completeness_score < t.sufficient_completeness:
        return (
            CLASS_INSUFFICIENT,
            f"Data completeness {completeness_score} is below the judgeability threshold "
            f"({t.sufficient_completeness}); absence of evidence is not evidence of absence.",
        )
    if evidence_score >= t.high_evidence:
        if has_suspicious:
            return (
                CLASS_NEEDS_REVIEW,
                "Evidence is strong but at least one claim looks unreliable; verify before trusting.",
            )
        return (
            CLASS_TRUSTED,
            f"Evidence score {evidence_score} meets the trust threshold ({t.high_evidence}) "
            "with sufficient data.",
        )
    if evidence_score <= t.low_evidence:
        return (
            CLASS_LIKELY_GAP,
            f"Record is well documented (completeness {completeness_score}) but shows no ICU "
            "evidence - likely a real capability gap at this facility.",
        )
    return (
        CLASS_NEEDS_REVIEW,
        f"Evidence score {evidence_score} is ambiguous (between {t.low_evidence} and "
        f"{t.high_evidence}); a human should inspect the fragments.",
    )


def score_facility(
    record: Mapping[str, Any],
    config: ScoringConfig | None = None,
    is_name_duplicate: bool = False,
) -> FacilityScore:
    """Extract evidence, validate, score and classify one facility record."""
    config = config or ScoringConfig()
    evidence = extract_evidence(record, config)
    flags = validate_facility(record, evidence, config, is_name_duplicate=is_name_duplicate)
    evidence_score, ev_components = compute_evidence_score(evidence, config)
    completeness_score, comp_components = compute_completeness_score(record, config)
    classification, reason = classify(
        evidence_score,
        completeness_score,
        has_contradiction=has_severity(flags, SEV_CONTRADICTION),
        has_suspicious=has_severity(flags, SEV_SUSPICIOUS),
        config=config,
    )
    return FacilityScore(
        capability_evidence_score=evidence_score,
        data_completeness_score=completeness_score,
        classification=classification,
        classification_reason=reason,
        evidence=evidence,
        validation_flags=flags,
        evidence_components=ev_components,
        completeness_components=comp_components,
    )


def _name_duplicate_mask(df: pd.DataFrame) -> pd.Series:
    """Mark rows whose normalised (name, city) pair occurs more than once."""
    name = df.get("name", pd.Series(index=df.index, dtype=object)).map(
        lambda v: (normalize_null_like(v) or "").lower()
    )
    city = df.get("address_city", pd.Series(index=df.index, dtype=object)).map(
        lambda v: (normalize_null_like(v) or "").lower()
    )
    key = name + "||" + city
    return key.duplicated(keep=False) & (name != "")


def score_dataframe(df: pd.DataFrame, config: ScoringConfig | None = None) -> pd.DataFrame:
    """Score every facility row; returns score columns aligned to ``df.index``.

    Nested structures (fragments, flags, components) are JSON-encoded so the
    result can be stored as Parquet and re-hydrated in the app.
    """
    config = config or ScoringConfig()
    dup_mask = _name_duplicate_mask(df)
    rows = []
    for idx, record in df.iterrows():
        s = score_facility(record.to_dict(), config, is_name_duplicate=bool(dup_mask.loc[idx]))
        rows.append(
            {
                "capability_evidence_score": s.capability_evidence_score,
                "data_completeness_score": s.data_completeness_score,
                "classification": s.classification,
                "classification_reason": s.classification_reason,
                "explicit_icu_claim": s.evidence.explicit_icu_claim,
                "icu_bed_count": s.evidence.icu_bed_count,
                "n_contradiction_flags": len(s.contradiction_flags),
                "n_validation_flags": len(s.validation_flags),
                "evidence_fragments_json": json.dumps(
                    [f.model_dump() for f in s.evidence.supporting_text_fragments]
                ),
                "missing_evidence_json": json.dumps(s.evidence.missing_evidence),
                "validation_flags_json": json.dumps([f.model_dump() for f in s.validation_flags]),
                "evidence_components_json": json.dumps(s.evidence_components),
                "completeness_components_json": json.dumps(s.completeness_components),
            }
        )
    return pd.DataFrame(rows, index=df.index)
