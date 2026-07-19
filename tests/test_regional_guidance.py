"""Regional guidance, decision path and evidence-policy copy (D24)."""

from __future__ import annotations

from caregap_map.config import (
    REGION_DATA_DESERT,
    REGION_NEEDS_REVIEW,
    REGION_PLANNING_GAP,
    REGION_TRUSTED,
    ScoringConfig,
)
from caregap_map.regional_guidance import (
    EVIDENCE_POLICY_CAPTION,
    decision_path,
    evidence_policy_lines,
    regional_guidance,
    reviewer_action,
)

ALL_STATUSES = [REGION_TRUSTED, REGION_NEEDS_REVIEW, REGION_PLANNING_GAP, REGION_DATA_DESERT]


def summary(**overrides) -> dict:
    base = {
        "facility_count": 56,
        "pct_sufficient_data": 100.0,
        "trusted_icu_count": 1,
        "needs_review_count": 23,
        "region_status": REGION_TRUSTED,
    }
    base.update(overrides)
    return base


class TestGuidance:
    def test_every_status_has_meaning_and_action(self):
        for status in ALL_STATUSES:
            g = regional_guidance(status)
            assert g.icon and g.meaning and g.action
            assert g.status == status

    def test_trusted_never_claims_adequate_population_coverage(self):
        g = regional_guidance(REGION_TRUSTED)
        assert "does not show that ICU capacity is adequate" in g.meaning
        assert "verify operational details" in g.action.lower()

    def test_needs_review_says_claims_not_safe_to_trust(self):
        g = regional_guidance(REGION_NEEDS_REVIEW)
        assert "none is currently safe to trust" in g.meaning
        assert "Review the flagged facilities" in g.action

    def test_planning_gap_includes_field_verification_warning(self):
        g = regional_guidance(REGION_PLANNING_GAP)
        assert "none contains credible ICU evidence" in g.meaning
        assert "field verification" in g.action
        assert "not proof that no ICU exists" in g.action

    def test_data_desert_prioritizes_data_collection(self):
        g = regional_guidance(REGION_DATA_DESERT)
        assert "too sparse or incomplete" in g.meaning
        assert "Prioritize data collection" in g.action
        assert "Do not classify this region as an ICU gap" in g.action

    def test_unknown_status_degrades_safely(self):
        g = regional_guidance("Weird Status")
        assert g.status == "Weird Status"
        assert g.meaning and g.action


class TestDecisionPath:
    def test_trusted_path_reflects_inputs(self):
        steps = decision_path(summary(), ScoringConfig())
        questions = [s.question for s in steps]
        assert questions == [
            "Enough records?",
            "Enough judgeable data?",
            "Trusted ICU evidence found?",
            "Unresolved ICU claims?",
            "Regional result",
        ]
        assert steps[0].outcome == "56 records"
        assert steps[1].outcome == "100% judgeable"
        assert steps[2].icon == "✅" and steps[2].outcome == "1 record"
        assert steps[3].icon == "⚠️" and steps[3].outcome == "23 records"
        assert steps[-1].outcome == REGION_TRUSTED
        assert steps[-1].icon == "🟢"

    def test_data_desert_path_stops_at_insufficient_data(self):
        steps = decision_path(
            summary(
                facility_count=8,
                pct_sufficient_data=12.0,
                trusted_icu_count=0,
                needs_review_count=0,
                region_status=REGION_DATA_DESERT,
            ),
            ScoringConfig(),
        )
        questions = [s.question for s in steps]
        # Stops after the failed judgeability check - no trust/review steps.
        assert questions == ["Enough records?", "Enough judgeable data?", "Regional result"]
        assert steps[1].icon == "❌"
        assert steps[-1].outcome == REGION_DATA_DESERT

    def test_too_few_records_stops_immediately(self):
        steps = decision_path(
            summary(facility_count=1, region_status=REGION_DATA_DESERT), ScoringConfig()
        )
        assert [s.question for s in steps] == ["Enough records?", "Regional result"]
        assert steps[0].icon == "❌"

    def test_planning_gap_path(self):
        steps = decision_path(
            summary(
                facility_count=17,
                pct_sufficient_data=94.0,
                trusted_icu_count=0,
                needs_review_count=0,
                region_status=REGION_PLANNING_GAP,
            ),
            ScoringConfig(),
        )
        by_question = {s.question: s for s in steps}
        assert by_question["Trusted ICU evidence found?"].outcome == "None"
        assert by_question["Trusted ICU evidence found?"].icon == "❌"
        assert by_question["Unresolved ICU claims?"].outcome == "None remaining"
        assert steps[-1].outcome == REGION_PLANNING_GAP

    def test_final_step_always_states_stored_status(self):
        for status in ALL_STATUSES:
            steps = decision_path(summary(region_status=status), ScoringConfig())
            assert steps[-1].question == "Regional result"
            assert steps[-1].outcome == status

    def test_thresholds_surface_in_details_not_outcomes(self):
        config = ScoringConfig()
        steps = decision_path(summary(), config)
        assert str(config.thresholds.region_min_facilities) in steps[0].detail
        assert f"{config.thresholds.region_min_data_pct:.0f}" in steps[1].detail


class TestReviewerActions:
    def test_all_classes_have_actions(self):
        from caregap_map.config import ALL_CLASSES

        for cls in ALL_CLASSES:
            assert reviewer_action(cls)

    def test_unknown_class_is_safe(self):
        assert "Inspect" in reviewer_action("???")


class TestEvidencePolicy:
    def test_states_complete_trusted_requirements(self):
        text = "\n".join(evidence_policy_lines(ScoringConfig()))
        assert "judgeable" in text
        assert "trust threshold" in text
        assert "explicit ICU claim" in text
        assert "2 distinct corroborating" in text
        assert "no contradiction or blocking suspicious flag" in text
        # All four displayed thresholds are present.
        assert "45" in text  # judgeability + trust threshold defaults
        assert "15" in text  # low-evidence default
        assert "40%" in text  # regional judgeability default

    def test_policy_is_not_a_planner_preference(self):
        assert "not planner preferences" in EVIDENCE_POLICY_CAPTION
        assert "not" in EVIDENCE_POLICY_CAPTION and "adjustable" in EVIDENCE_POLICY_CAPTION
