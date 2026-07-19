"""Planning readiness: a transparent checklist, separate from judgeability.

Three deliberately separate concepts (DECISIONS D20):

- **Record judgeability** (``data_completeness_score``): are the supplied
  record's fields populated enough to evaluate what the record claims?
- **ICU evidence strength** (``capability_evidence_score`` + class): how
  strongly does the record support ICU capability?
- **Planning readiness** (this module): does the record carry the fields a
  planner would actually need to act on it?

The audit (reports/headline_metric_audit.md) showed why judgeability must
not be sold as planning readiness: ~99% of records are judgeable, yet 75%
lack capacity and 64% lack a doctor count. Planning readiness is therefore
a visible checklist over planner-useful fields - not another opaque score,
and it never changes a record's classification.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from .cleaning import parse_coordinates, parse_int_safe, parse_list_field
from .config import CLASS_LIKELY_GAP, CLASS_TRUSTED

# Component key -> short user-facing description. Order is display order.
PLANNING_COMPONENTS: dict[str, str] = {
    "usable_coordinates": "valid coordinates for India",
    "resolved_district": "district resolved (PIN directory)",
    "source_url": "at least one source URL",
    "total_capacity": "total bed capacity stated",
    "doctor_count": "number of doctors stated",
    "determinate_icu_evidence": "evidence rules reached a definite answer (trusted / no evidence)",
}

LEVEL_LOW = "Low"
LEVEL_MEDIUM = "Medium"
LEVEL_HIGH = "High"


class PlanningReadiness(BaseModel):
    """Checklist outcome for one facility record. Every component is visible."""

    components: dict[str, bool]
    available: int
    total: int
    level: str

    @property
    def summary(self) -> str:
        return f"{self.available} of {self.total} planning fields available"


def readiness_level(available: int, total: int = len(PLANNING_COMPONENTS)) -> str:
    """Map a component count to Low / Medium / High (fixed, documented bands)."""
    if available >= total - 1:
        return LEVEL_HIGH
    if available >= total // 2:
        return LEVEL_MEDIUM
    return LEVEL_LOW


def assess_planning_readiness(record: Mapping[str, Any]) -> PlanningReadiness:
    """Evaluate the planning-readiness checklist for one record.

    Works on scored parquet rows (preferring the cleaned ``*_int`` /
    ``coord_status`` columns) and on raw records (falling back to parsing).
    Purely descriptive: the result never feeds classification.
    """
    coord_status = record.get("coord_status")
    if coord_status is None:
        _, _, coord_status = parse_coordinates(record.get("latitude"), record.get("longitude"))

    capacity = record.get("capacity_int")
    if capacity is None or capacity != capacity:  # missing column or NaN
        capacity = parse_int_safe(record.get("capacity"))
    doctors = record.get("number_doctors_int")
    if doctors is None or doctors != doctors:
        doctors = parse_int_safe(record.get("numberDoctors"))

    district = record.get("district_final")
    has_district = district is not None and district == district and str(district).strip() != ""

    components = {
        "usable_coordinates": coord_status == "ok",
        "resolved_district": bool(has_district),
        "source_url": bool(parse_list_field(record.get("source_urls"))),
        "total_capacity": capacity is not None,
        "doctor_count": doctors is not None,
        "determinate_icu_evidence": record.get("classification") in (CLASS_TRUSTED, CLASS_LIKELY_GAP),
    }
    available = sum(components.values())
    total = len(components)
    return PlanningReadiness(
        components=components,
        available=available,
        total=total,
        level=readiness_level(available, total),
    )
