"""Operational data availability + automated assessment (D23 semantics)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from caregap_map.config import (
    ALL_CLASSES,
    CLASS_INSUFFICIENT,
    CLASS_LIKELY_GAP,
    CLASS_NEEDS_REVIEW,
    CLASS_TRUSTED,
)
from caregap_map.planning import (
    LEVEL_HIGH,
    LEVEL_LOW,
    LEVEL_MEDIUM,
    OPERATIONAL_COMPONENTS,
    assess_operational_data,
    assessment_status,
    availability_level,
)
from caregap_map.scoring import score_facility


def parquet_row(**overrides) -> dict:
    """A scored-parquet-shaped row with everything available."""
    row = {
        "coord_status": "ok",
        "state_final": "Maharashtra",
        "district_final": "Raigad",
        "source_urls": '["https://example.org"]',
        "capacity_int": 120,
        "number_doctors_int": 12,
        "icu_bed_count": 10,
        "icu_subtypes_json": json.dumps(["general_or_unspecified"]),
        "corroboration_categories_json": json.dumps(["equipment", "staffing"]),
        "validation_flags_json": "[]",
        "classification": CLASS_TRUSTED,
    }
    row.update(overrides)
    return row


class TestLocationResolved:
    def test_location_is_one_component_not_two(self):
        assert list(OPERATIONAL_COMPONENTS).count("location_resolved") == 1
        assert "resolved_district" not in OPERATIONAL_COMPONENTS
        assert "usable_coordinates" not in OPERATIONAL_COMPONENTS
        # coords AND district present still contribute exactly one point vs
        # a record with neither.
        full = assess_operational_data(parquet_row())
        none = assess_operational_data(
            parquet_row(coord_status="unparseable", state_final=None, district_final=None)
        )
        assert full.available - none.available == 1

    def test_valid_coordinates_alone_satisfy(self):
        data = assess_operational_data(parquet_row(state_final=None, district_final=None))
        assert data.components["location_resolved"] is True
        assert "Coordinates: available" in data.details["location_resolved"]
        assert "not resolved" in data.details["location_resolved"]

    def test_state_district_without_coordinates_satisfy(self):
        data = assess_operational_data(parquet_row(coord_status="out_of_range"))
        assert data.components["location_resolved"] is True
        assert "Maharashtra / Raigad" in data.details["location_resolved"]

    def test_missing_both_fails(self):
        data = assess_operational_data(
            parquet_row(coord_status="unparseable", state_final=None, district_final=None)
        )
        assert data.components["location_resolved"] is False

    def test_district_alone_without_state_does_not_satisfy(self):
        data = assess_operational_data(parquet_row(coord_status="unparseable", state_final=None))
        assert data.components["location_resolved"] is False


class TestSourceProvenance:
    def test_source_url_satisfies_with_caveat(self):
        data = assess_operational_data(parquet_row())
        assert data.components["source_or_provenance"] is True
        assert "does not guarantee" in data.details["source_or_provenance"]

    def test_no_source_fails(self):
        data = assess_operational_data(parquet_row(source_urls="[]"))
        assert data.components["source_or_provenance"] is False

    def test_directory_content_raises_source_warning(self):
        flags = json.dumps(
            [{"name": "directory_or_partner_content_detected", "severity": "suspicious", "detail": "x"}]
        )
        data = assess_operational_data(parquet_row(validation_flags_json=flags))
        assert data.source_warning is not None
        assert "directory" in data.source_warning

    def test_clean_record_has_no_source_warning(self):
        assert assess_operational_data(parquet_row()).source_warning is None


class TestCapacityAndDoctors:
    def test_total_capacity_never_counts_as_icu_beds(self):
        data = assess_operational_data(parquet_row(icu_bed_count=None))
        assert data.components["total_capacity"] is True
        assert data.components["anchored_icu_bed_count"] is False
        assert "not ICU capacity" in data.details["total_capacity"]

    def test_doctor_count_is_facility_wide_wording(self):
        data = assess_operational_data(parquet_row())
        assert data.components["doctor_count"] is True
        assert "not ICU staffing" in data.details["doctor_count"]


class TestAnchoredIcuBedCount:
    def test_precomputed_anchored_count_satisfies(self):
        data = assess_operational_data(parquet_row())
        assert data.components["anchored_icu_bed_count"] is True
        assert "10 beds" in data.details["anchored_icu_bed_count"]

    def test_raw_record_anchored_fragment_satisfies(self):
        raw = {
            "description": "A 10-bedded ICU with round-the-clock staff.",
            "source_urls": "[]",
        }
        data = assess_operational_data(raw)
        assert data.components["anchored_icu_bed_count"] is True

    def test_unanchored_numbers_do_not_satisfy(self):
        raw = {
            "description": "We have 10 ventilators. ICU available.",
            "source_urls": "[]",
        }
        data = assess_operational_data(raw)
        assert data.components["anchored_icu_bed_count"] is False
        assert "never count" in data.details["anchored_icu_bed_count"]

    def test_nicu_bed_count_preserves_subtype(self):
        data = assess_operational_data(
            parquet_row(icu_bed_count=5, icu_subtypes_json=json.dumps(["neonatal_icu"]))
        )
        detail = data.details["anchored_icu_bed_count"]
        assert "5 beds" in detail
        assert "NICU" in detail
        assert "does not automatically imply" in detail

    def test_general_subtype_has_no_specialised_caveat(self):
        detail = assess_operational_data(parquet_row()).details["anchored_icu_bed_count"]
        assert "does not automatically imply" not in detail


class TestIcuOperationalDetail:
    def test_independent_categories_satisfy(self):
        data = assess_operational_data(parquet_row())
        assert data.components["icu_operational_detail"] is True
        assert "equipment" in data.details["icu_operational_detail"]

    def test_bed_count_alone_does_not_satisfy_item_six(self):
        data = assess_operational_data(
            parquet_row(corroboration_categories_json=json.dumps(["bed_count"]))
        )
        assert data.components["icu_operational_detail"] is False

    def test_generic_content_does_not_satisfy(self):
        raw = {
            "description": "A modern dental practice.",
            "procedure": '["root canal"]',
            "equipment": '["dental X-ray"]',
            "source_urls": "[]",
        }
        data = assess_operational_data(raw)
        assert data.components["icu_operational_detail"] is False

    def test_verified_independent_detail_on_raw_record_satisfies(self):
        raw = {
            "description": "ICU with ventilator support and trained ICU nursing team.",
            "procedure": "[]",
            "equipment": '["ventilator"]',
            "source_urls": "[]",
        }
        data = assess_operational_data(raw)
        assert data.components["icu_operational_detail"] is True


class TestLevelsAndNeutrality:
    @pytest.mark.parametrize(
        ("available", "expected"),
        [
            (0, LEVEL_LOW),
            (2, LEVEL_LOW),
            (3, LEVEL_MEDIUM),
            (4, LEVEL_MEDIUM),
            (5, LEVEL_HIGH),
            (6, LEVEL_HIGH),
        ],
    )
    def test_bands(self, available, expected):
        assert availability_level(available, 6) == expected

    def test_summary_wording(self):
        data = assess_operational_data(parquet_row())
        assert data.summary == "High — 6 of 6 operational fields available"

    def test_checklist_never_changes_classification(self):
        record = {
            "name": "Pearl Dental Clinic",
            "description": "A modern dental practice.",
            "procedure": '["root canal"]',
            "equipment": '["dental X-ray"]',
            "source_urls": '["https://example.org"]',
            "latitude": "10.0",
            "longitude": "76.0",
        }
        before = score_facility(record)
        snapshot = dict(record)
        assess_operational_data(record)
        assert record == snapshot  # no mutation
        after = score_facility(record)
        assert after.classification == before.classification
        assert after.capability_evidence_score == before.capability_evidence_score
        assert after.data_completeness_score == before.data_completeness_score


class TestAssessmentStatus:
    def test_all_four_classes_map(self):
        trusted = assessment_status(CLASS_TRUSTED)
        assert trusted.resolved is True
        assert "Trusted ICU evidence" in trusted.headline
        assert "not current real-world ICU operation" in trusted.help_text

        gap = assessment_status(CLASS_LIKELY_GAP)
        assert gap.resolved is True
        assert "No ICU evidence in judgeable record" in gap.headline
        assert "not proof" in gap.help_text

        review = assessment_status(CLASS_NEEDS_REVIEW)
        assert review.resolved is False
        assert "Human review required" in review.headline

        insufficient = assessment_status(CLASS_INSUFFICIENT)
        assert insufficient.resolved is False
        assert "Insufficient data" in insufficient.headline

    def test_every_class_has_icon_and_help(self):
        for cls in ALL_CLASSES:
            status = assessment_status(cls)
            assert status.icon and status.help_text

    def test_unknown_class_is_safe(self):
        status = assessment_status("Something Else")
        assert status.resolved is False
        assert "unknown" in status.headline.lower()


class TestWordingSafety:
    BANNED = [
        "icu availability",
        "confirmed icu",
        "actual icu",
        "verified icu",
        "real icu coverage",
        "national icu coverage",
        "icu confirmed",
        "has an icu",
    ]

    def test_app_ui_source_contains_no_unsafe_wording(self):
        root = Path(__file__).resolve().parents[1]
        for rel in (
            "app.py",
            "src/caregap_map/regional_guidance.py",
            "src/caregap_map/ui_components.py",
        ):
            source = (root / rel).read_text(encoding="utf-8").lower()
            for phrase in self.BANNED:
                assert phrase not in source, f"unsafe phrase {phrase!r} in {rel}"

    def test_config_labels_contain_no_unsafe_wording(self):
        from caregap_map.config import FACILITY_DISPLAY_LABELS, REGION_DISCLAIMER, SUBTYPE_LABELS

        text = " ".join(
            [*FACILITY_DISPLAY_LABELS.values(), *SUBTYPE_LABELS.values(), REGION_DISCLAIMER]
        ).lower()
        for phrase in self.BANNED:
            assert phrase not in text
