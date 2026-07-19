"""Trusted-classification calibration (D14): independent corroboration required.

Derived from the manual LLM-disagreement review: one phrase containing
"critical care" used to double-count as explicit claim + procedure
corroboration and alone reach the Trusted bar.
"""

import json

from caregap_map.config import CLASS_NEEDS_REVIEW, CLASS_TRUSTED, ScoringConfig
from caregap_map.evidence import extract_evidence
from caregap_map.scoring import count_corroboration_categories, score_facility
from conftest import make_record


class TestIndependentCorroboration:
    def test_same_phrase_does_not_corroborate_itself(self, config):
        # The Fortis-Kangra pattern: "Critical Care" in a specialty list
        # matches both the explicit and the procedure group.
        record = make_record(
            capability=json.dumps(
                ["Emergency & Critical Care services", "Departments include Critical Care Medicine"]
            )
        )
        ev = extract_evidence(record, config)
        assert ev.explicit_icu_claim
        n, categories = count_corroboration_categories(ev, config)
        assert "procedure" not in categories
        s = score_facility(record, config)
        assert s.classification == CLASS_NEEDS_REVIEW
        assert "corroborat" in s.classification_reason

    def test_staff_list_mention_is_not_trusted(self, config):
        # The Kirloskar pattern: incidental "intensive care" inside a staff list.
        record = make_record(
            capability=json.dumps(
                ["Staff include surgeons and multidisciplinary team (anesthesia & intensive care)"]
            )
        )
        s = score_facility(record, config)
        assert s.classification == CLASS_NEEDS_REVIEW

    def test_distinct_keywords_in_one_sentence_do_corroborate(self, config):
        # "ICU" and "ventilator" in one sentence are different evidence, not
        # the same phrase counted twice.
        record = make_record(
            capability=json.dumps(["24x7 ICU setup with ventilator facility"]),
            equipment=json.dumps(["ICU with ventilator facility", "Defibrillator"]),
        )
        ev = extract_evidence(record, config)
        n, categories = count_corroboration_categories(ev, config)
        assert "equipment" in categories

    def test_explicit_plus_two_categories_is_trusted(self, config):
        record = make_record(
            description="Tertiary hospital with a 20-bed intensive care unit.",
            equipment=json.dumps(["Ventilator x 10"]),
        )
        s = score_facility(record, config)
        assert s.classification == CLASS_TRUSTED  # equipment + anchored bed count
        assert len(s.corroboration_categories) >= 2

    def test_explicit_plus_one_category_needs_review(self, config):
        # D14 principle preserved under policy v2 (D28): WITHOUT a
        # substantive description statement, one operational category
        # remains one short - claim lives only in structured fields here.
        record = make_record(
            capability=json.dumps(["ICU"]),
            equipment=json.dumps(["Ventilator available"]),
        )
        s = score_facility(record, config)
        assert s.capability_evidence_score >= config.thresholds.high_evidence
        assert s.description_corroboration is False
        assert s.classification == CLASS_NEEDS_REVIEW

    def test_substantive_description_plus_one_category_is_trusted_v2(self, config):
        # WITH a substantive description statement echoed by a structured
        # claim, the record promotes under evidence policy v2 (variant B):
        # description corroboration + equipment.
        record = make_record(
            description="The hospital has an ICU.",
            capability=json.dumps(["ICU"]),
            equipment=json.dumps(["Ventilator available"]),
        )
        s = score_facility(record, config)
        assert s.description_corroboration is True
        assert s.classification == CLASS_TRUSTED
        assert "policy v2" in s.classification_reason

    def test_description_only_claim_stays_review_under_v2b(self, config):
        # Substantive prose without any structured-field echo stays review -
        # the narrowest audited rule (D28) requires both.
        record = make_record(
            description="The hospital has an ICU.",
            equipment=json.dumps(["Ventilator available"]),
        )
        s = score_facility(record, config)
        assert s.description_corroboration is False
        assert s.classification == CLASS_NEEDS_REVIEW

    def test_min_categories_is_configurable(self):
        lenient = ScoringConfig()
        lenient.thresholds.min_corroboration_categories = 1
        record = make_record(
            description="The hospital has an ICU.",
            equipment=json.dumps(["Ventilator available"]),
        )
        assert score_facility(record, lenient).classification == CLASS_TRUSTED

    def test_signals_without_explicit_claim_never_trusted(self):
        cfg = ScoringConfig()
        cfg.thresholds.high_evidence = 30  # force the score over the bar
        record = make_record(
            equipment=json.dumps(["Ventilator", "Defibrillator"]),
            procedure=json.dumps(["Mechanical ventilation"]),
        )
        s = score_facility(record, cfg)
        assert not s.evidence.explicit_icu_claim
        assert s.classification == CLASS_NEEDS_REVIEW
        assert "explicit" in s.classification_reason
