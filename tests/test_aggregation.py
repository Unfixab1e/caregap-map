"""Regional aggregation: medical gaps vs data deserts must stay distinct."""

import pandas as pd

from caregap_map.aggregation import UNASSIGNED, aggregate_regions
from caregap_map.config import (
    CLASS_INSUFFICIENT,
    CLASS_LIKELY_GAP,
    CLASS_NEEDS_REVIEW,
    CLASS_TRUSTED,
    REGION_DATA_DESERT,
    REGION_NEEDS_REVIEW,
    REGION_PLANNING_GAP,
    REGION_TRUSTED,
    ScoringConfig,
)


def scored_row(state, district, classification, evidence, completeness):
    return {
        "state_final": state,
        "district_final": district,
        "classification": classification,
        "capability_evidence_score": evidence,
        "data_completeness_score": completeness,
    }


def build_scored() -> pd.DataFrame:
    rows = []
    # State A: one trusted ICU among documented facilities.
    rows += [
        scored_row("StateA", "D1", CLASS_TRUSTED, 80, 90),
        scored_row("StateA", "D1", CLASS_LIKELY_GAP, 0, 90),
        scored_row("StateA", "D2", CLASS_LIKELY_GAP, 5, 80),
    ]
    # State B: well documented, no ICU evidence anywhere -> likely gap.
    rows += [
        scored_row("StateB", "D1", CLASS_LIKELY_GAP, 0, 95),
        scored_row("StateB", "D1", CLASS_LIKELY_GAP, 5, 90),
        scored_row("StateB", "D2", CLASS_LIKELY_GAP, 0, 85),
    ]
    # State C: records exist but are unjudgeable -> data desert, NOT a gap.
    rows += [
        scored_row("StateC", "D1", CLASS_INSUFFICIENT, 0, 20),
        scored_row("StateC", "D1", CLASS_INSUFFICIENT, 35, 30),
        scored_row("StateC", "D2", CLASS_INSUFFICIENT, 0, 10),
    ]
    # State D: too few records to say anything.
    rows += [scored_row("StateD", "D1", CLASS_LIKELY_GAP, 0, 90)]
    # State E: no trusted facility, ambiguous claims outstanding.
    rows += [
        scored_row("StateE", "D1", CLASS_NEEDS_REVIEW, 35, 90),
        scored_row("StateE", "D1", CLASS_LIKELY_GAP, 0, 90),
        scored_row("StateE", "D2", CLASS_LIKELY_GAP, 0, 85),
    ]
    # A facility with no resolvable region must not vanish.
    rows += [scored_row(None, None, CLASS_INSUFFICIENT, 0, 10)]
    return pd.DataFrame(rows)


class TestRegionalClassification:
    def test_counts_per_class(self):
        out = aggregate_regions(build_scored(), "state").set_index("state")
        a = out.loc["StateA"]
        assert a["facility_count"] == 3
        assert a["trusted_icu_count"] == 1
        assert a["likely_gap_count"] == 2

    def test_trusted_state_wording_is_evidence_not_coverage(self):
        out = aggregate_regions(build_scored(), "state").set_index("state")
        assert out.loc["StateA", "region_status"] == REGION_TRUSTED
        # The reason must never imply sufficient coverage.
        assert "NOT mean coverage is sufficient" in out.loc["StateA", "region_status_reason"]

    def test_documented_absence_is_planning_gap(self):
        out = aggregate_regions(build_scored(), "state").set_index("state")
        assert out.loc["StateB", "region_status"] == REGION_PLANNING_GAP

    def test_unjudgeable_records_are_data_desert_not_gap(self):
        out = aggregate_regions(build_scored(), "state").set_index("state")
        assert out.loc["StateC", "region_status"] == REGION_DATA_DESERT
        assert out.loc["StateC", "region_status"] != REGION_PLANNING_GAP
        # Evidence coverage and data coverage are reported independently.
        assert out.loc["StateC", "data_coverage_pct"] == 0.0

    def test_too_few_records_is_data_desert(self):
        out = aggregate_regions(build_scored(), "state").set_index("state")
        assert out.loc["StateD", "region_status"] == REGION_DATA_DESERT

    def test_pending_reviews_block_gap_label(self):
        out = aggregate_regions(build_scored(), "state").set_index("state")
        assert out.loc["StateE", "region_status"] == REGION_NEEDS_REVIEW

    def test_unassigned_region_is_kept(self):
        out = aggregate_regions(build_scored(), "state")
        assert UNASSIGNED in set(out["state"])

    def test_district_level_keys(self):
        out = aggregate_regions(build_scored(), "district")
        assert {"state", "district"} <= set(out.columns)
        state_a = out[out["state"] == "StateA"]
        assert set(state_a["district"]) == {"D1", "D2"}


class TestTrustWeighting:
    def test_poorly_documented_claims_move_needle_less(self):
        strong_docs = pd.DataFrame(
            [
                scored_row("S", "D", CLASS_TRUSTED, 80, 100),
                scored_row("S", "D", CLASS_LIKELY_GAP, 0, 100),
                scored_row("S", "D", CLASS_LIKELY_GAP, 0, 100),
            ]
        )
        weak_docs = pd.DataFrame(
            [
                scored_row("S", "D", CLASS_NEEDS_REVIEW, 80, 50),
                scored_row("S", "D", CLASS_LIKELY_GAP, 0, 100),
                scored_row("S", "D", CLASS_LIKELY_GAP, 0, 100),
            ]
        )
        cfg = ScoringConfig()
        strong = aggregate_regions(strong_docs, "state", cfg)["trust_weighted_icu_coverage"][0]
        weak = aggregate_regions(weak_docs, "state", cfg)["trust_weighted_icu_coverage"][0]
        assert strong > weak
