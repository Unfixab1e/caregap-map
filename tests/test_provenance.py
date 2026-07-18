"""Dataset-generation provenance rules (D18).

Upstream, capability/procedure/equipment were generated together in one
extraction pass (text + images), specialty tags can derive from the facility
name alone, and multi-facility page content can leak into records. These
tests lock the consequences into scoring and validation.
"""

import json

from caregap_map.config import CLASS_NEEDS_REVIEW, CLASS_TRUSTED, ScoringConfig
from caregap_map.evidence import extract_evidence
from caregap_map.scoring import count_corroboration_categories, score_facility
from conftest import make_record


class TestSpecialtyTagsAreContext:
    def test_critical_care_specialty_alone_is_not_an_icu_claim(self, config):
        record = make_record(specialties=json.dumps(["criticalCareMedicine"]))
        ev = extract_evidence(record, config)
        assert not ev.explicit_icu_claim
        assert ev.specialty_context_signals
        # Traceability preserved: the tag is still a visible fragment.
        assert any(f.group == "specialty_context" for f in ev.supporting_text_fragments)

    def test_specialty_only_record_lands_in_review_not_gap_not_trust(self, config):
        record = make_record(specialties=json.dumps(["criticalCareMedicine"]))
        s = score_facility(record, config)
        assert s.classification == CLASS_NEEDS_REVIEW
        assert s.evidence_components.get("specialty_context") == 20
        assert "explicit_claim" not in s.evidence_components

    def test_specialty_context_adds_nothing_when_real_claim_exists(self, config):
        record = make_record(
            description="Runs a 10-bed ICU with ventilators.",
            specialties=json.dumps(["criticalCareMedicine"]),
            equipment=json.dumps(["Ventilator x 4"]),
        )
        s = score_facility(record, config)
        assert "explicit_claim" in s.evidence_components
        assert "specialty_context" not in s.evidence_components

    def test_pediatrics_specialty_creates_no_picu_or_nicu_evidence(self, config):
        record = make_record(specialties=json.dumps(["pediatrics", "neonatologyPerinatalMedicine"]))
        ev = extract_evidence(record, config)
        assert not ev.explicit_icu_claim
        assert ev.icu_subtypes == []

    def test_llm_quoted_specialty_tag_is_reclassified(self, config):
        from caregap_map.llm_extraction import LlmEvidenceExtractor
        from test_llm_extraction import StubClient, llm_payload

        record = make_record(specialties=json.dumps(["criticalCareMedicine"]))
        client = StubClient(
            llm_payload(
                explicit_icu_claim=True,
                fragments=[
                    {
                        "field": "specialties",
                        "group": "explicit_icu",
                        "quote": "criticalCareMedicine",
                    }
                ],
            )
        )
        result = LlmEvidenceExtractor(client, config).extract(record)
        assert not result.explicit_icu_claim
        assert result.specialty_context_signals


class TestFieldSemantics:
    def test_total_capacity_is_never_icu_capacity(self, config):
        record = make_record(capacity="200", description="Large multi-speciality hospital.")
        ev = extract_evidence(record, config)
        assert ev.icu_bed_count is None  # 200 total beds are not ICU beds

    def test_total_doctor_count_is_not_icu_staffing_evidence(self, config):
        record = make_record(numberDoctors="150")
        ev = extract_evidence(record, config)
        assert ev.staffing_signals == []
        s = score_facility(record, config)
        assert "staffing" not in s.evidence_components  # evidence side untouched
        assert "staffing" in s.completeness_components  # data-presence side only


class TestCrossFieldConsistency:
    def test_multi_field_agreement_is_not_a_corroboration_category(self, config):
        # Explicit claim echoed across three generated fields, nothing else.
        record = make_record(
            description="The hospital has an ICU.",
            capability=json.dumps(["ICU available"]),
            specialties=json.dumps(["criticalCareMedicine"]),
        )
        ev = extract_evidence(record, config)
        n, categories = count_corroboration_categories(ev, config)
        assert "multi_field" not in categories
        assert n == 0
        s = score_facility(record, config)
        assert s.classification == CLASS_NEEDS_REVIEW

    def test_score_component_is_named_cross_field_consistency(self, config):
        record = make_record(
            description="20-bed intensive care unit on site.",
            capability=json.dumps(["ICU with ventilator support"]),
            equipment=json.dumps(["ICU ventilators"]),
        )
        s = score_facility(record, config)
        assert "cross_field_consistency" in s.evidence_components
        assert "multi_field_bonus" not in s.evidence_components

    def test_distinct_categories_still_reach_trusted(self, config):
        record = make_record(
            description="Tertiary hospital with a 20-bed intensive care unit.",
            equipment=json.dumps(["Ventilator x 10"]),
        )
        s = score_facility(record, config)
        assert s.classification == CLASS_TRUSTED  # equipment + anchored bed count
        assert set(s.corroboration_categories) == {"equipment", "bed_count"}


class TestCrossOrganizationContent:
    def test_directory_content_flagged_for_review(self, config):
        record = make_record(
            description="Well documented hospital.",
            capability=json.dumps(
                [
                    "Other Hospital is listed as a referral hospital with a 30-bed ICU",
                    "ICU with ventilator support",
                ]
            ),
            equipment=json.dumps(["Ventilator"]),
        )
        s = score_facility(record, config)
        assert any(f.name == "directory_or_partner_content_detected" for f in s.validation_flags)
        # Suspicious severity: a would-be Trusted record routes to review.
        assert s.classification == CLASS_NEEDS_REVIEW

    def test_clean_records_not_flagged(self, config):
        record = make_record(
            description="Tertiary hospital with a 20-bed intensive care unit.",
            equipment=json.dumps(["Ventilator x 10"]),
        )
        s = score_facility(record, config)
        assert not any(f.name == "directory_or_partner_content_detected" for f in s.validation_flags)


class TestWordingStaysHonest:
    def test_classification_reasons_do_not_claim_independence(self, config):
        record = make_record(
            description="Tertiary hospital with a 20-bed intensive care unit.",
            equipment=json.dumps(["Ventilator x 10"]),
        )
        s = score_facility(record, config)
        assert "independent" not in s.classification_reason.lower()
        assert "supplied record" in s.classification_reason

    def test_scoring_stays_deterministic(self, config):
        record = make_record(description="Runs a 10-bed ICU with ventilators.")
        first = score_facility(record, config)
        second = score_facility(record, ScoringConfig())
        assert first.capability_evidence_score == second.capability_evidence_score
        assert first.classification == second.classification
