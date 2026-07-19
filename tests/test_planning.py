"""Planning-readiness checklist (separate from record judgeability)."""

from __future__ import annotations

import pytest

from caregap_map.config import CLASS_LIKELY_GAP, CLASS_NEEDS_REVIEW, CLASS_TRUSTED
from caregap_map.planning import (
    LEVEL_HIGH,
    LEVEL_LOW,
    LEVEL_MEDIUM,
    PLANNING_COMPONENTS,
    assess_planning_readiness,
    readiness_level,
)


def full_record(**overrides) -> dict:
    record = {
        "coord_status": "ok",
        "district_final": "Ernakulam",
        "source_urls": '["https://example.org"]',
        "capacity_int": 120,
        "number_doctors_int": 12,
        "classification": CLASS_TRUSTED,
    }
    record.update(overrides)
    return record


class TestChecklist:
    def test_fully_ready_record(self):
        r = assess_planning_readiness(full_record())
        assert r.available == r.total == len(PLANNING_COMPONENTS)
        assert r.level == LEVEL_HIGH
        assert all(r.components.values())
        assert r.summary == f"{r.total} of {r.total} planning fields available"

    def test_empty_record_is_low(self):
        r = assess_planning_readiness({})
        assert r.available == 0
        assert r.level == LEVEL_LOW
        assert not any(r.components.values())

    def test_components_are_documented(self):
        r = assess_planning_readiness(full_record())
        assert set(r.components) == set(PLANNING_COMPONENTS)

    def test_judgeable_gap_record_can_still_be_unready(self):
        # The audit's central case: judgeable, classified, but planner-thin.
        r = assess_planning_readiness(
            full_record(
                capacity_int=None,
                number_doctors_int=None,
                classification=CLASS_LIKELY_GAP,
            )
        )
        assert r.components["determinate_icu_evidence"] is True
        assert r.available == 4
        assert r.level == LEVEL_MEDIUM

    def test_needs_review_is_not_determinate(self):
        r = assess_planning_readiness(full_record(classification=CLASS_NEEDS_REVIEW))
        assert r.components["determinate_icu_evidence"] is False

    def test_falls_back_to_raw_fields(self):
        r = assess_planning_readiness(
            {
                "latitude": "10.1",
                "longitude": "76.3",
                "district_final": "Ernakulam",
                "source_urls": '["https://example.org"]',
                "capacity": "60",
                "numberDoctors": "5",
                "classification": CLASS_TRUSTED,
            }
        )
        assert r.available == r.total

    def test_nan_district_and_capacity_do_not_count(self):
        r = assess_planning_readiness(full_record(district_final=float("nan"), capacity_int=float("nan")))
        assert r.components["resolved_district"] is False
        assert r.components["total_capacity"] is False


class TestLevels:
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
        assert readiness_level(available, 6) == expected
