"""Operational data availability + automated-assessment status (drilldown).

Four deliberately separate concepts (DECISIONS D20, D23):

- **ICU evidence strength** (``capability_evidence_score``): how strongly
  the supplied record supports an ICU capability claim.
- **Record judgeability** (``data_completeness_score``): whether the
  supplied record is populated enough to evaluate what it claims.
- **Operational data availability** (this module): whether location,
  provenance, capacity, staffing and ICU-specific operational details are
  available for planning. A transparent six-item checklist - descriptive
  only, never fed into classification, and NOT a clinically validated
  planning-readiness standard.
- **Automated evidence assessment** (:func:`assessment_status`): what the
  deterministic rules concluded and whether a human still needs to review
  the record.

The module file keeps its historical name (``planning.py``) for
compatibility; the public API uses the operational-data wording.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, Field

from .cleaning import parse_coordinates, parse_int_safe, parse_list_field
from .config import (
    CLASS_INSUFFICIENT,
    CLASS_LIKELY_GAP,
    CLASS_NEEDS_REVIEW,
    CLASS_TRUSTED,
    SUBTYPE_GENERAL,
    SUBTYPE_LABELS,
    ScoringConfig,
)

# Component key -> user-facing checklist label. Order is display order.
# Location counts ONCE (coordinates OR resolved state+district), capacity
# and doctors are explicitly TOTAL-facility figures (never ICU capacity or
# ICU staffing), the ICU bed count must be source-anchored, and the
# operational-detail item accepts only the independent corroboration
# categories staffing/equipment/procedure (the anchored bed count is item
# 5, and a phrase that matched the explicit-claim pattern never counts).
OPERATIONAL_COMPONENTS: dict[str, str] = {
    "location_resolved": "location resolved",
    "source_or_provenance": "source or provenance available",
    "total_capacity": "total facility bed capacity stated",
    "doctor_count": "total doctor count stated",
    "anchored_icu_bed_count": "source-anchored ICU bed count stated",
    "icu_operational_detail": "ICU-relevant staffing, equipment or procedure detail stated",
}

# Corroboration categories (see scoring.count_corroboration_categories)
# that satisfy the ICU-relevant operational-detail item. bed_count is
# deliberately excluded - it is its own checklist item.
OPERATIONAL_DETAIL_CATEGORIES = ("staffing", "equipment", "procedure")

LEVEL_LOW = "Low"
LEVEL_MEDIUM = "Medium"
LEVEL_HIGH = "High"

OPERATIONAL_HELP = (
    "Shows whether this supplied record contains the location, provenance, "
    "capacity, staffing and ICU-specific operational details a planner would "
    "need. It does not verify that the ICU is currently open or operational, "
    "and it is not a clinically validated planning-readiness standard."
)

SOURCE_CAVEAT = (
    "A source reference is available, but source presence does not guarantee "
    "that the claim is current, first-party or reliable."
)


class OperationalData(BaseModel):
    """Checklist outcome for one facility record. Every component visible."""

    components: dict[str, bool]
    available: int
    total: int
    level: str
    # Per-component detail lines for the expanded help view.
    details: dict[str, str] = Field(default_factory=dict)
    # Set when the source looks like directory/aggregator/partner content.
    source_warning: str | None = None

    @property
    def summary(self) -> str:
        return f"{self.level} — {self.available} of {self.total} operational fields available"


def availability_level(available: int, total: int = len(OPERATIONAL_COMPONENTS)) -> str:
    """Map a component count to Low / Medium / High (fixed, documented bands)."""
    if available >= total - 1:
        return LEVEL_HIGH
    if available >= total // 2:
        return LEVEL_MEDIUM
    return LEVEL_LOW


def _clean_value(record: Mapping[str, Any], key: str) -> Any:
    value = record.get(key)
    if value is None or value != value:  # missing or NaN
        return None
    return value


def _corroboration_categories(record: Mapping[str, Any], config: ScoringConfig) -> list[str]:
    """Prefer the precomputed parquet column; fall back to re-extraction."""
    raw = record.get("corroboration_categories_json")
    if isinstance(raw, str) and raw:
        return list(json.loads(raw))
    from .evidence import extract_evidence
    from .scoring import count_corroboration_categories

    _, categories = count_corroboration_categories(extract_evidence(record, config), config)
    return categories


def _icu_bed_count(record: Mapping[str, Any], config: ScoringConfig) -> int | None:
    value = _clean_value(record, "icu_bed_count")
    if value is not None:
        return int(value)
    if "icu_bed_count" in record:  # column present but empty: anchoring found nothing
        return None
    from .evidence import extract_evidence

    return extract_evidence(record, config).icu_bed_count


def _subtypes(record: Mapping[str, Any], config: ScoringConfig) -> list[str]:
    raw = record.get("icu_subtypes_json")
    if isinstance(raw, str) and raw:
        return list(json.loads(raw))
    return []


def assess_operational_data(
    record: Mapping[str, Any], config: ScoringConfig | None = None
) -> OperationalData:
    """Evaluate the six operational-data items for one record.

    Works on scored parquet rows (preferred: uses the precomputed columns)
    and on raw records (falls back to parsing / re-extraction). Purely
    descriptive: the result never feeds classification or aggregation.
    """
    config = config or ScoringConfig()
    details: dict[str, str] = {}

    # 1. Location resolved - coordinates OR resolved state+district; ONE point.
    coord_status = record.get("coord_status")
    if coord_status is None:
        _, _, coord_status = parse_coordinates(record.get("latitude"), record.get("longitude"))
    has_coords = coord_status == "ok"
    state = _clean_value(record, "state_final")
    district = _clean_value(record, "district_final")
    has_district = bool(state and str(state).strip()) and bool(district and str(district).strip())
    location_resolved = has_coords or has_district
    details["location_resolved"] = (
        f"Coordinates: {'available' if has_coords else 'not usable'} · "
        f"District: {f'{state} / {district}' if has_district else 'not resolved'}"
    )

    # 2. Source or provenance available (a URL is a reference, not trust).
    urls = parse_list_field(record.get("source_urls"))
    has_source = bool(urls)
    details["source_or_provenance"] = (
        f"{len(urls)} source URL(s). {SOURCE_CAVEAT}" if has_source else "No source reference."
    )
    source_warning = None
    flags_raw = record.get("validation_flags_json")
    if isinstance(flags_raw, str) and flags_raw:
        flag_names = {f.get("name") for f in json.loads(flags_raw)}
        if "directory_or_partner_content_detected" in flag_names:
            source_warning = (
                "Source content looks like directory/aggregator/partner material "
                "that may describe a different organization."
            )

    # 3./4. TOTAL facility figures - never ICU capacity or ICU staffing.
    capacity = _clean_value(record, "capacity_int")
    if capacity is None:
        capacity = parse_int_safe(record.get("capacity"))
    doctors = _clean_value(record, "number_doctors_int")
    if doctors is None:
        doctors = parse_int_safe(record.get("numberDoctors"))
    details["total_capacity"] = (
        f"{int(capacity)} total inpatient beds (facility-wide, not ICU capacity)"
        if capacity is not None
        else "Not stated."
    )
    details["doctor_count"] = (
        f"{int(doctors)} doctors in total (facility-wide, not ICU staffing)"
        if doctors is not None
        else "Not stated."
    )

    # 5. Source-anchored ICU bed count (number + bed word + ICU context in
    # ONE verified fragment; separate numbers or totals never count).
    bed_count = _icu_bed_count(record, config)
    subtypes = _subtypes(record, config)
    specialised_only = bool(subtypes) and SUBTYPE_GENERAL not in subtypes
    if bed_count is not None:
        pretty = ", ".join(SUBTYPE_LABELS.get(s, s) for s in subtypes)
        details["anchored_icu_bed_count"] = f"{bed_count} beds anchored in intensive-care context" + (
            f" — subtype evidence: {pretty} only; this does not automatically imply "
            "general adult ICU beds"
            if specialised_only
            else ""
        )
    else:
        details["anchored_icu_bed_count"] = (
            "No bed count anchored to ICU context (total capacity or separate "
            "numbers never count)."
        )

    # 6. ICU-relevant operational detail: independent corroboration in
    # staffing/equipment/procedure only (existing independence rules).
    categories = _corroboration_categories(record, config)
    operational = sorted(c for c in categories if c in OPERATIONAL_DETAIL_CATEGORIES)
    details["icu_operational_detail"] = (
        f"Independent ICU-relevant detail: {', '.join(operational)}"
        if operational
        else "No independent ICU-relevant staffing/equipment/procedure detail "
        "(generic content never counts)."
    )

    components = {
        "location_resolved": location_resolved,
        "source_or_provenance": has_source,
        "total_capacity": capacity is not None,
        "doctor_count": doctors is not None,
        "anchored_icu_bed_count": bed_count is not None,
        "icu_operational_detail": bool(operational),
    }
    available = sum(components.values())
    return OperationalData(
        components=components,
        available=available,
        total=len(components),
        level=availability_level(available, len(components)),
        details=details,
        source_warning=source_warning,
    )


# ---------------------------------------------------------------------------
# Trust requirements (evidence policy v2 gate display, D28)
# ---------------------------------------------------------------------------


class TrustGate(BaseModel):
    label: str
    met: bool


def trust_requirements(
    record: Mapping[str, Any], config: ScoringConfig | None = None
) -> tuple[list[TrustGate], str]:
    """The six Trusted gates for one scored row, plus a corroboration line.

    Display-only: reads the stored scores/flags and explains why a record
    is green or still yellow. The corroboration line names the accepted
    categories ("Substantive description evidence + equipment") instead of
    a bare count.
    """
    config = config or ScoringConfig()
    t = config.thresholds
    flags = json.loads(record.get("validation_flags_json") or "[]")
    has_suspicious = any(f.get("severity") == "suspicious" for f in flags)
    has_contradiction = int(record.get("n_contradiction_flags") or 0) > 0
    categories = list(json.loads(record.get("corroboration_categories_json") or "[]"))
    n = len(categories)
    description_corroboration = bool(record.get("description_corroboration") or False)
    corroboration_met = n >= t.min_corroboration_categories or (
        description_corroboration and n >= 1
    )

    gates = [
        TrustGate(
            label="Record is judgeable",
            met=int(record.get("data_completeness_score") or 0) >= t.sufficient_completeness,
        ),
        TrustGate(label="Explicit ICU claim", met=bool(record.get("explicit_icu_claim"))),
        TrustGate(
            label="Evidence score reaches threshold",
            met=int(record.get("capability_evidence_score") or 0) >= t.high_evidence,
        ),
        TrustGate(label="No blocking contradiction", met=not has_contradiction),
        TrustGate(label="No blocking suspicious claim", met=not has_suspicious),
        TrustGate(label="Corroboration requirement met", met=corroboration_met),
    ]

    if n >= t.min_corroboration_categories:
        explanation = f"Corroboration accepted through: {' + '.join(categories)}"
    elif description_corroboration and n >= 1:
        explanation = (
            f"Corroboration accepted through: substantive description evidence + "
            f"{' + '.join(categories)} (evidence policy v2)"
        )
    else:
        explanation = (
            f"{n} of {t.min_corroboration_categories} operational categories present"
            + (f" ({' + '.join(categories)})" if categories else "")
            + " — a substantive description statement plus one operational category "
            "would also satisfy the requirement (evidence policy v2)."
        )
    return gates, explanation


# ---------------------------------------------------------------------------
# Automated evidence assessment (separate from the checklist)
# ---------------------------------------------------------------------------


class AssessmentStatus(BaseModel):
    """What the deterministic rules concluded for one record."""

    resolved: bool
    icon: str
    headline: str
    help_text: str


_ASSESSMENT = {
    CLASS_TRUSTED: AssessmentStatus(
        resolved=True,
        icon="🟢",
        headline="Assessment resolved: Trusted ICU evidence",
        help_text=(
            "The record meets the configured evidence, explicit-claim and "
            "independent corroboration requirements. This verifies evidence in "
            "the supplied record, not current real-world ICU operation."
        ),
    ),
    CLASS_LIKELY_GAP: AssessmentStatus(
        resolved=True,
        icon="🔴",
        headline="Assessment resolved: No ICU evidence in judgeable record",
        help_text=(
            "The supplied record is populated enough to inspect, but the current "
            "rules found no credible ICU evidence. This is not proof that the "
            "facility lacks an ICU."
        ),
    ),
    CLASS_NEEDS_REVIEW: AssessmentStatus(
        resolved=False,
        icon="🟡",
        headline="Assessment unresolved: Human review required",
        help_text=(
            "The deterministic rules could not settle this record - see the "
            "reason and validator flags below."
        ),
    ),
    CLASS_INSUFFICIENT: AssessmentStatus(
        resolved=False,
        icon="⚪",
        headline="Assessment unresolved: Insufficient data",
        help_text=(
            "The supplied record is too incomplete to infer either trusted ICU "
            "evidence or absence of ICU evidence."
        ),
    ),
}


def assessment_status(classification: str) -> AssessmentStatus:
    """Status block content for a stored classification (all four classes)."""
    status = _ASSESSMENT.get(classification)
    if status is None:
        return AssessmentStatus(
            resolved=False,
            icon="⚪",
            headline=f"Assessment status unknown: {classification}",
            help_text="Unrecognised classification value.",
        )
    return status
