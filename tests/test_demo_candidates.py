"""Demo-candidate finder: selection logic on synthetic data, read-only."""

from __future__ import annotations

import json

import pandas as pd

from caregap_map.config import (
    CLASS_LIKELY_GAP,
    CLASS_NEEDS_REVIEW,
    CLASS_TRUSTED,
    REGION_DATA_DESERT,
    REGION_PLANNING_GAP,
    REGION_TRUSTED,
)
from caregap_map.demo_candidates import find_demo_candidates, render_markdown


def facility(uid, cls, subtypes=None, evidence=0, name="Hospital X"):
    return {
        "unique_id": uid,
        "name": name,
        "state_final": "Kerala",
        "district_final": "Ernakulam",
        "classification": cls,
        "icu_subtypes_json": json.dumps(subtypes or []),
        "capability_evidence_score": evidence,
        "data_completeness_score": 80,
    }


def district(name, status, count=5, trusted=0):
    return {
        "state": "Kerala",
        "district": name,
        "region_status": status,
        "facility_count": count,
        "trusted_icu_count": trusted,
        "pct_sufficient_data": 90.0,
    }


def make_inputs():
    scored = pd.DataFrame(
        [
            facility("t1", CLASS_TRUSTED, ["general_or_unspecified"], 90),
            facility("t2", CLASS_TRUSTED, ["neonatal_icu"], 80),  # NICU-only
            facility("t3", CLASS_TRUSTED, ["neonatal_icu", "general_or_unspecified"], 85),
            facility("r1", CLASS_NEEDS_REVIEW, [], 35),
            facility("g1", CLASS_LIKELY_GAP, [], 0),
        ]
    )
    regions = pd.DataFrame(
        [
            district("Desert", REGION_DATA_DESERT, count=2),
            district("Gap", REGION_PLANNING_GAP, count=8),
            district("Single", REGION_TRUSTED, count=10, trusted=1),
            district("Multi", REGION_TRUSTED, count=10, trusted=4),
        ]
    )
    return scored, regions


class TestFindDemoCandidates:
    def test_categories_populated_correctly(self):
        scored, regions = make_inputs()
        out = find_demo_candidates(scored, regions)

        assert [c["unique_id"] for c in out["trusted_general_icu"]] == ["t1", "t3"]
        assert out["needs_human_review"][0]["unique_id"] == "r1"
        # subtype-ONLY: t3 has general too and must not appear.
        assert [c["unique_id"] for c in out["nicu_only"]] == ["t2"]
        assert out["picu_only"] == []
        assert out["data_desert_district"][0]["district"] == "Desert"
        assert out["planning_gap_district"][0]["district"] == "Gap"
        # single-trusted: only the district whose status rests on ONE record.
        assert [d["district"] for d in out["single_trusted_record_district"]] == ["Single"]
        scopes = {c["scope"] for c in out["persistence_test_data"]}
        assert scopes == {"facility", "district"}

    def test_read_only(self):
        scored, regions = make_inputs()
        before = scored.copy(deep=True)
        find_demo_candidates(scored, regions)
        pd.testing.assert_frame_equal(scored, before)

    def test_markdown_renders_all_categories(self):
        scored, regions = make_inputs()
        out = find_demo_candidates(scored, regions)
        md = render_markdown(out)
        for category in out:
            assert f"## {category}" in md
        assert "git-ignored" in md
        assert "never changes classifications" in md
