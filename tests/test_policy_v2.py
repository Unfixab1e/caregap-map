"""Evidence policy v2 (D28): substantive description corroboration.

A substantive description-level ICU statement plus ONE operational
category satisfies the Trusted corroboration requirement; bare keywords,
list-only claims, directory text and description-only evidence never do.
"""

from __future__ import annotations

import pytest

from caregap_map.config import CLASS_NEEDS_REVIEW, CLASS_TRUSTED, ScoringConfig
from caregap_map.scoring import (
    classify,
    find_substantive_description_claim,
    is_substantive_icu_text,
    score_facility,
)


def record(description: str, **overrides) -> dict:
    base = {
        "name": "Test Hospital",
        "description": description,
        "capability": "[]",
        "specialties": "[]",
        "procedure": "[]",
        "equipment": "[]",
        "source_urls": '["https://example.org"]',
        "latitude": "10.0",
        "longitude": "76.0",
        "capacity": "120",
        "numberDoctors": "12",
    }
    base.update(overrides)
    return base


CONFIG = ScoringConfig()


class TestSubstantiveDetection:
    @pytest.mark.parametrize(
        "text",
        [
            "The hospital offers ICU, NICU, critical care and ventilator support.",
            "A dedicated intensive-care unit provides mechanical ventilation and continuous monitoring.",
            "The hospital operates a 10-bed ICU with ventilator support.",
            "it offers ICU, NICU, critical care, ventilator support, and general medical services",
        ],
    )
    def test_substantive_statements_qualify(self, text):
        assert is_substantive_icu_text(text, CONFIG) is True

    @pytest.mark.parametrize(
        "text",
        [
            "ICU available",
            "Critical care",
            "ICU / NICU",
            "Multispecialty hospital, ICU",
            "Best ICU hospital",
            "Listed hospitals with ICU facilities",
            "ICU and critical care available",  # no verb/unit/detail: bare list
        ],
    )
    def test_bare_labels_never_qualify(self, text):
        assert is_substantive_icu_text(text, CONFIG) is False

    def test_directory_text_never_qualifies(self):
        assert (
            is_substantive_icu_text(
                "Directory of hospitals offering ICU services with ventilator support", CONFIG
            )
            is False
        )

    def test_not_a_character_count_rule(self):
        # Long but bare: many words, still no verb/unit/operational detail.
        long_bare = "ICU NICU PICU cardiology neurology orthopaedics dermatology radiology unit-free list"
        assert is_substantive_icu_text(long_bare, CONFIG) is False


class TestDescriptionCorroborationSignal:
    def test_claim_in_capability_only_gives_no_signal(self):
        score = score_facility(
            record(
                "A multispeciality hospital serving the region.",
                capability='["Critical Care Department with ICU facilities"]',
            )
        )
        assert score.description_corroboration is False

    def test_substantive_description_alone_is_not_the_v2b_signal(self):
        # v2B: substantive prose WITHOUT the claim in any structured field
        # does not produce the corroboration signal (narrowest audited rule).
        score = score_facility(
            record("The hospital offers ICU care with ventilator support around the clock.")
        )
        assert find_substantive_description_claim(score.evidence, CONFIG) is not None
        assert score.description_corroboration is False
        assert score.classification == CLASS_NEEDS_REVIEW

    def test_substantive_description_plus_structured_claim_gives_signal(self):
        score = score_facility(
            record(
                "The hospital offers ICU care with ventilator support around the clock.",
                capability='["ICU"]',
            )
        )
        assert score.description_corroboration is True


class TestClassifyPolicyGate:
    def test_description_plus_one_category_is_trusted(self):
        cls, reason = classify(
            80, 100, False, False, CONFIG,
            explicit_claim=True, corroboration_categories=1, description_corroboration=True,
        )
        assert cls == CLASS_TRUSTED
        assert "policy v2" in reason

    def test_description_alone_is_never_trusted(self):
        cls, reason = classify(
            80, 100, False, False, CONFIG,
            explicit_claim=True, corroboration_categories=0, description_corroboration=True,
        )
        assert cls == CLASS_NEEDS_REVIEW
        assert "additional corroboration required" in reason

    def test_policy_v1_behavior_is_reproducible(self):
        cls, _ = classify(
            80, 100, False, False, CONFIG,
            explicit_claim=True, corroboration_categories=1, description_corroboration=False,
        )
        assert cls == CLASS_NEEDS_REVIEW  # v1: one category is one short

    def test_two_categories_still_trusted_without_description(self):
        cls, _ = classify(
            80, 100, False, False, CONFIG,
            explicit_claim=True, corroboration_categories=2, description_corroboration=False,
        )
        assert cls == CLASS_TRUSTED

    def test_suspicious_blocks_v2_promotion(self):
        cls, _ = classify(
            80, 100, False, True, CONFIG,
            explicit_claim=True, corroboration_categories=1, description_corroboration=True,
        )
        assert cls == CLASS_NEEDS_REVIEW

    def test_contradiction_blocks_v2_promotion(self):
        cls, _ = classify(
            80, 100, True, False, CONFIG,
            explicit_claim=True, corroboration_categories=1, description_corroboration=True,
        )
        assert cls == CLASS_NEEDS_REVIEW


class TestRegressionCases:
    def test_satyarthi_like_promotion(self):
        """Substantive description ICU/NICU claim + ventilator equipment +
        repeated structured claim, no blocking flags -> Trusted under v2."""
        score = score_facility(
            record(
                "Led by Dr. Gaurav Satyarthi, it offers ICU, NICU, critical care, "
                "ventilator support, and general medical and surgical services.",
                capability='["ICU", "NICU"]',
            )
        )
        assert score.description_corroboration is True
        assert score.evidence.explicit_icu_claim is True
        assert "equipment" in score.corroboration_categories  # ventilator
        assert score.classification == CLASS_TRUSTED
        assert "policy v2" in score.classification_reason
        # NICU evidence stays accurately labelled alongside the general claim.
        assert "neonatal_icu" in score.evidence.icu_subtypes

    def test_sudarshan_like_capability_only_stays_review(self):
        """Explicit claim only in capability, ventilator evidence, no
        substantive description claim, one category -> Needs Human Review."""
        score = score_facility(
            record(
                "A well known multispeciality hospital in the city.",
                capability='["Critical Care Department with ICU facilities"]',
                equipment='["ventilator"]',
            )
        )
        assert score.description_corroboration is False
        assert len(score.corroboration_categories) == 1
        assert score.classification == CLASS_NEEDS_REVIEW
        assert "additional corroboration required" in score.classification_reason

    def test_bare_description_keyword_stays_review(self):
        score = score_facility(record("ICU available"))
        assert score.description_corroboration is False
        assert score.classification == CLASS_NEEDS_REVIEW

    def test_directory_text_stays_review(self):
        score = score_facility(
            record("Directory of hospitals offering ICU services with ventilator support.")
        )
        assert score.classification == CLASS_NEEDS_REVIEW
        assert score.description_corroboration is False

    def test_description_with_contradiction_stays_review(self):
        score = score_facility(
            record(
                "The hospital offers ICU care with ventilator support.",
                capability='["ICU"]',
                procedure='["No ICU available on weekends"]',
            )
        )
        assert score.classification == CLASS_NEEDS_REVIEW

    def test_substantive_nicu_only_description(self):
        score = score_facility(
            record(
                "The hospital operates a dedicated NICU with ventilator support for newborns.",
                capability='["NICU"]',
            )
        )
        assert score.classification == CLASS_TRUSTED
        assert score.evidence.icu_subtypes == ["neonatal_icu"]  # never silently general

    def test_same_phrase_does_not_corroborate_itself(self):
        """'ICU and critical care available' must not count as claim +
        description corroboration + procedure corroboration."""
        score = score_facility(record("ICU and critical care available"))
        assert score.description_corroboration is False
        assert "procedure" not in score.corroboration_categories
        assert score.classification == CLASS_NEEDS_REVIEW


class TestTrustRequirementsDisplay:
    def _row(self, **overrides) -> dict:
        base = {
            "data_completeness_score": 100,
            "explicit_icu_claim": True,
            "capability_evidence_score": 80,
            "n_contradiction_flags": 0,
            "validation_flags_json": "[]",
            "corroboration_categories_json": '["equipment"]',
            "description_corroboration": True,
        }
        base.update(overrides)
        return base

    def test_v2_promotion_names_the_accepted_categories(self):
        from caregap_map.planning import trust_requirements

        gates, line = trust_requirements(self._row())
        assert all(g.met for g in gates)
        assert "substantive description evidence + equipment" in line
        assert "policy v2" in line

    def test_two_categories_named_without_bare_counts(self):
        from caregap_map.planning import trust_requirements

        gates, line = trust_requirements(
            self._row(
                corroboration_categories_json='["equipment", "staffing"]',
                description_corroboration=False,
            )
        )
        assert all(g.met for g in gates)
        assert "equipment + staffing" in line

    def test_one_short_record_shows_unmet_gate_and_guidance(self):
        from caregap_map.planning import trust_requirements

        gates, line = trust_requirements(self._row(description_corroboration=False))
        by_label = {g.label: g.met for g in gates}
        assert by_label["Corroboration requirement met"] is False
        assert by_label["Evidence score reaches threshold"] is True
        assert "substantive description statement plus one" in line

    def test_suspicious_gate_reflected(self):
        from caregap_map.planning import trust_requirements

        gates, _ = trust_requirements(
            self._row(
                validation_flags_json='[{"name": "x", "severity": "suspicious", "detail": "d"}]'
            )
        )
        assert {g.label: g.met for g in gates}["No blocking suspicious claim"] is False

    def test_missing_v2_column_defaults_safely(self):
        from caregap_map.planning import trust_requirements

        row = self._row()
        del row["description_corroboration"]
        gates, _ = trust_requirements(row)
        assert {g.label: g.met for g in gates}["Corroboration requirement met"] is False
