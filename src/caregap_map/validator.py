"""Deterministic validation of facility records.

These checks evaluate *dataset consistency*, not clinical truth. Each flag
records a severity used by classification:

- ``contradiction``: the record contradicts itself -> Needs Human Review
- ``suspicious``: a claim looks unreliable -> score penalty; a would-be
  "Trusted" record is demoted to Needs Human Review
- ``data_quality``: recorded and displayed, but does not force a class
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from .cleaning import normalize_null_like, parse_coordinates, parse_int_safe, parse_list_field
from .config import ScoringConfig
from .evidence import EvidenceResult

SEV_CONTRADICTION = "contradiction"
SEV_SUSPICIOUS = "suspicious"
SEV_DATA_QUALITY = "data_quality"


class ValidationFlag(BaseModel):
    """One validator finding, with the severity classification consumes."""

    name: str
    severity: str
    detail: str


def validate_facility(
    record: Mapping[str, Any],
    evidence: EvidenceResult,
    config: ScoringConfig,
    is_name_duplicate: bool = False,
) -> list[ValidationFlag]:
    """Run all deterministic validators for one facility record.

    ``record`` must hold the raw field values; if the cleaning pipeline
    already attached ``coord_status`` / ``geo_conflict`` those are used,
    otherwise they are derived on the fly.
    """
    flags: list[ValidationFlag] = []

    # 1. Self-contradiction: text negates ICU while a claim exists elsewhere.
    for name in evidence.contradiction_flags:
        flags.append(
            ValidationFlag(
                name=name,
                severity=SEV_CONTRADICTION,
                detail="A text field negates ICU/critical-care capability; see negation fragments.",
            )
        )

    # 2. Suspicious claims found during extraction (e.g. ICU beds > capacity).
    for name in evidence.suspicious_claim_flags:
        flags.append(
            ValidationFlag(
                name=name,
                severity=SEV_SUSPICIOUS,
                detail="Claimed numbers are internally inconsistent.",
            )
        )

    # 3. ICU claim without any free-text support (claim only in structured lists).
    if evidence.explicit_icu_claim:
        explicit_fields = {f.field for f in evidence.supporting_text_fragments if f.group == "explicit_icu"}
        has_description = normalize_null_like(record.get("description")) is not None
        if has_description and explicit_fields and explicit_fields <= {"capability", "specialties"}:
            # Informational: descriptions are often one-liners, so a claim
            # living only in the structured fields is common and not by
            # itself suspicious. Surfaced to reviewers, no class demotion.
            flags.append(
                ValidationFlag(
                    name="icu_claim_not_in_description",
                    severity=SEV_DATA_QUALITY,
                    detail="ICU appears only in structured claim fields, not in the description text.",
                )
            )

        # 4. ICU claim with zero corroboration from equipment/procedure/staffing/beds.
        if (
            not evidence.equipment_signals
            and not evidence.procedure_signals
            and not evidence.staffing_signals
            and evidence.icu_bed_count is None
        ):
            flags.append(
                ValidationFlag(
                    name="icu_claim_uncorroborated",
                    severity=SEV_SUSPICIOUS,
                    detail="Explicit ICU claim with no equipment, procedure, staffing or bed-count support.",
                )
            )

    # 5. Coordinate validity (invalid values are flagged, never dropped).
    coord_status = record.get("coord_status")
    if coord_status is None:
        _, _, coord_status = parse_coordinates(record.get("latitude"), record.get("longitude"))
    if coord_status in ("unparseable", "out_of_range"):
        flags.append(
            ValidationFlag(
                name="invalid_coordinates",
                severity=SEV_DATA_QUALITY,
                detail=f"Coordinates are {coord_status} for India.",
            )
        )

    # 6. Suspiciously sparse record.
    populated = 0
    if normalize_null_like(record.get("description")) is not None:
        populated += 1
    for field in ("procedure", "equipment", "capability", "specialties"):
        if parse_list_field(record.get(field)):
            populated += 1
    if parse_int_safe(record.get("capacity")) is not None:
        populated += 1
    if parse_int_safe(record.get("numberDoctors")) is not None:
        populated += 1
    if populated < config.sparse_record_min_fields:
        flags.append(
            ValidationFlag(
                name="suspiciously_sparse_record",
                severity=SEV_DATA_QUALITY,
                detail=f"Only {populated} of 7 key evidence fields are populated.",
            )
        )

    # 7. Possible duplicate facility (same normalised name + city elsewhere).
    if is_name_duplicate:
        flags.append(
            ValidationFlag(
                name="possible_duplicate_facility",
                severity=SEV_DATA_QUALITY,
                detail="Another record shares this facility name and city.",
            )
        )

    # 8. ICU evidence that appears to describe ANOTHER organization. The
    #    upstream pipeline extracted organizations from multi-facility pages
    #    (directories, referral/empanelment lists, partner pages), so such
    #    content can leak into a record. Review, never auto-delete.
    for frag in evidence.supporting_text_fragments:
        if frag.group == "negation":
            continue
        if any(re.search(p, frag.text, re.IGNORECASE) for p in config.keywords.cross_organization):
            flags.append(
                ValidationFlag(
                    name="directory_or_partner_content_detected",
                    severity=SEV_SUSPICIOUS,
                    detail=(
                        "ICU-relevant text looks like directory/referral/partner content "
                        "that may describe a different organization: "
                        f"{frag.text[:120]!r}"
                    ),
                )
            )
            break

    # 9. Geography conflict between the PIN directory and the state field.
    if bool(record.get("geo_conflict")):
        flags.append(
            ValidationFlag(
                name="state_field_conflicts_with_pin_directory",
                severity=SEV_DATA_QUALITY,
                detail=(
                    f"State field resolves to {record.get('state_from_field')!r} but the PIN code "
                    f"maps to {record.get('state_from_pin')!r}."
                ),
            )
        )

    return flags


def has_severity(flags: list[ValidationFlag], severity: str) -> bool:
    """True if any flag carries the given severity."""
    return any(f.severity == severity for f in flags)
