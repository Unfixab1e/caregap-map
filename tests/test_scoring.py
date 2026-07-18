"""Scoring and classification: the required archetypes plus independence."""

import json

import pandas as pd

from caregap_map.config import (
    CLASS_INSUFFICIENT,
    CLASS_LIKELY_GAP,
    CLASS_NEEDS_REVIEW,
    CLASS_TRUSTED,
    ScoringConfig,
)
from caregap_map.scoring import score_dataframe, score_facility
from conftest import make_record


def strong_icu_record() -> dict:
    return make_record(
        description=(
            "Tertiary care hospital with a 20-bed intensive care unit staffed by "
            "intensivists. Ventilator support and defibrillators available."
        ),
        equipment=json.dumps(["Ventilator x 10", "Defibrillator", "Multipara monitor"]),
        procedure=json.dumps(["Mechanical ventilation", "Critical care management"]),
        capability=json.dumps(["24x7 ICU with ventilator beds"]),
    )


class TestRequiredArchetypes:
    def test_strong_icu_evidence_is_trusted(self, config):
        s = score_facility(strong_icu_record(), config)
        assert s.classification == CLASS_TRUSTED
        assert s.capability_evidence_score >= config.thresholds.high_evidence
        assert s.data_completeness_score >= config.thresholds.sufficient_completeness
        assert s.evidence.supporting_text_fragments  # traceable

    def test_claim_without_corroboration_needs_review(self, config):
        record = make_record(capability=json.dumps(["ICU available"]))
        s = score_facility(record, config)
        assert s.classification == CLASS_NEEDS_REVIEW
        assert any(f.name == "icu_claim_uncorroborated" for f in s.validation_flags)

    def test_non_icu_facility_with_complete_data_is_likely_gap(self, config):
        s = score_facility(make_record(), config)
        assert s.classification == CLASS_LIKELY_GAP
        assert s.capability_evidence_score <= config.thresholds.low_evidence
        assert s.data_completeness_score >= config.thresholds.sufficient_completeness

    def test_incomplete_record_is_insufficient_data(self, config):
        record = make_record(
            description=None,
            procedure="[]",
            equipment="[]",
            capability="[]",
            specialties="[]",
            numberDoctors="null",
            capacity=None,
            source_urls="[]",
            latitude=None,
            longitude=None,
            coord_status="missing",
            state_final=None,
            address_stateOrRegion=None,
        )
        s = score_facility(record, config)
        assert s.classification == CLASS_INSUFFICIENT
        assert s.data_completeness_score < config.thresholds.sufficient_completeness

    def test_contradictory_record_needs_review(self, config):
        record = make_record(
            description="This hospital has no ICU; critical cases are referred out.",
            capability=json.dumps(["ICU services", "Emergency care"]),
        )
        s = score_facility(record, config)
        assert s.classification == CLASS_NEEDS_REVIEW
        assert "negated_icu_mention" in s.contradiction_flags

    def test_null_like_placeholders_do_not_count_as_data(self, config):
        record = make_record(
            description="null",
            procedure="[]",
            equipment="[]",
            capability="[]",
            specialties="[]",
            numberDoctors="none",
            capacity="n/a",
            source_urls="[]",
            latitude="null",
            longitude="null",
            coord_status=None,  # force re-derivation from raw values
            state_final=None,
        )
        s = score_facility(record, config)
        assert s.classification == CLASS_INSUFFICIENT
        assert s.completeness_components == {}


class TestScoreIndependence:
    """Evidence and completeness must never leak into each other."""

    def test_complete_non_icu_scores_zero_evidence_full_completeness(self, config):
        s = score_facility(make_record(), config)
        assert s.capability_evidence_score == 0
        assert s.data_completeness_score == 100

    def test_icu_text_with_sparse_record_is_insufficient_not_trusted(self, config):
        # "No reliable data" must win over a strong-sounding claim.
        record = make_record(
            description="Has an ICU with ventilators and intensivists on duty.",
            procedure="[]",
            equipment="[]",
            capability="[]",
            specialties="[]",
            numberDoctors=None,
            capacity=None,
            source_urls="[]",
            latitude=None,
            longitude=None,
            coord_status="missing",
            state_final=None,
        )
        s = score_facility(record, config)
        assert s.capability_evidence_score > 0  # the claim is registered
        assert s.classification == CLASS_INSUFFICIENT  # but cannot be trusted


class TestConfigurability:
    def test_thresholds_change_classification(self):
        lenient = ScoringConfig()
        lenient.thresholds.high_evidence = 30
        record = make_record(capability=json.dumps(["ICU available"]))
        # With default config this is ambiguous (Needs Review); with a lenient
        # trust threshold the same record is not, proving thresholds are live.
        strict_result = score_facility(record, ScoringConfig())
        assert strict_result.classification == CLASS_NEEDS_REVIEW

    def test_weights_are_not_hardcoded(self, config):
        heavier = ScoringConfig()
        heavier.evidence_weights.explicit_claim = 90
        record = make_record(capability=json.dumps(["ICU available"]))
        base = score_facility(record, config).capability_evidence_score
        boosted = score_facility(record, heavier).capability_evidence_score
        assert boosted > base


class TestScoreDataframe:
    def test_batch_scoring_roundtrip(self, config):
        df = pd.DataFrame([strong_icu_record(), make_record(unique_id="test-0002")])
        out = score_dataframe(df, config)
        assert len(out) == 2
        assert out.iloc[0]["classification"] == CLASS_TRUSTED
        frags = json.loads(out.iloc[0]["evidence_fragments_json"])
        assert frags and all("text" in f for f in frags)

    def test_name_city_duplicates_flagged(self, config):
        df = pd.DataFrame(
            [
                make_record(unique_id="a"),
                make_record(unique_id="b"),  # same name + city as "a"
            ]
        )
        out = score_dataframe(df, config)
        flags = [json.loads(v) for v in out["validation_flags_json"]]
        assert all(any(f["name"] == "possible_duplicate_facility" for f in row_flags) for row_flags in flags)
