"""Display-label mapping: user-facing wording vs stored class constants."""

from __future__ import annotations

from caregap_map.config import (
    ALL_CLASSES,
    CLASS_INSUFFICIENT,
    CLASS_LIKELY_GAP,
    CLASS_NEEDS_REVIEW,
    CLASS_TRUSTED,
    FACILITY_DISPLAY_LABELS,
    REGION_PLANNING_GAP,
    facility_display_label,
)


def test_every_class_has_a_display_label():
    assert set(FACILITY_DISPLAY_LABELS) == set(ALL_CLASSES)


def test_gap_display_label_is_precise():
    label = facility_display_label(CLASS_LIKELY_GAP)
    assert label == "No ICU evidence in judgeable record"
    # The display label must not read as a real-world claim.
    assert "gap" not in label.lower()
    assert "medical" not in label.lower()


def test_no_display_label_claims_coverage():
    # "Coverage" is reserved for geographic/population coverage, which the
    # dataset cannot measure.
    for label in FACILITY_DISPLAY_LABELS.values():
        assert "coverage" not in label.lower()


def test_trusted_display_label_says_evidence():
    assert facility_display_label(CLASS_TRUSTED) == "Trusted ICU evidence"


def test_stored_constants_are_unchanged():
    # Parquet files, tests and history depend on the stored values.
    assert CLASS_TRUSTED == "Trusted ICU Coverage"
    assert CLASS_LIKELY_GAP == "Likely Medical Gap"
    assert CLASS_NEEDS_REVIEW == "Needs Human Review"
    assert CLASS_INSUFFICIENT == "Insufficient Data"


def test_region_statuses_pass_through_unchanged():
    assert facility_display_label(REGION_PLANNING_GAP) == REGION_PLANNING_GAP
    assert facility_display_label("anything else") == "anything else"
