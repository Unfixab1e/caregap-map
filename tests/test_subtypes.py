"""ICU subtype semantics: NICU/PICU/ICCU are never displayed as general ICU."""

import json

from conftest import make_record

from caregap_map.config import SUBTYPE_GENERAL
from caregap_map.evidence import extract_evidence


class TestSubtypeDetection:
    def test_unqualified_icu_is_general_or_unspecified(self, config):
        record = make_record(capability=json.dumps(["24x7 ICU with ventilator support"]))
        ev = extract_evidence(record, config)
        assert ev.icu_subtypes == [SUBTYPE_GENERAL]

    def test_nicu_only_is_not_general(self, config):
        record = make_record(
            capability=json.dumps(["Level III NICU with 20 beds"]),
        )
        ev = extract_evidence(record, config)
        assert ev.icu_subtypes == ["neonatal_icu"]
        assert SUBTYPE_GENERAL not in ev.icu_subtypes

    def test_multiple_subtypes_coexist(self, config):
        record = make_record(
            capability=json.dumps(
                [
                    "Paediatric Intensive Care Unit (PICU)",
                    "Surgical ICU for post-operative care",
                    "General ICU available",
                ]
            )
        )
        ev = extract_evidence(record, config)
        assert "pediatric_icu" in ev.icu_subtypes
        assert "surgical_icu" in ev.icu_subtypes
        # "General ICU available" carries no specialised qualifier -> general.
        assert SUBTYPE_GENERAL in ev.icu_subtypes

    def test_cardiac_iccu(self, config):
        record = make_record(description="The hospital runs an ICCU for cardiac patients.")
        ev = extract_evidence(record, config)
        assert ev.icu_subtypes == ["cardiac_icu"]

    def test_spelling_variants(self, config):
        record = make_record(description="Dedicated pediatric intensive care services.")
        ev = extract_evidence(record, config)
        assert "pediatric_icu" in ev.icu_subtypes

    def test_no_claim_no_subtypes(self, config):
        ev = extract_evidence(make_record(), config)
        assert ev.icu_subtypes == []


class TestLlmSubtypes:
    def test_llm_fragments_get_same_subtype_semantics(self, config):
        from test_llm_extraction import StubClient, llm_payload

        from caregap_map.llm_extraction import LlmEvidenceExtractor

        record = make_record(capability=json.dumps(["Level III NICU with ventilators"]))
        client = StubClient(
            llm_payload(
                explicit_icu_claim=True,
                fragments=[
                    {
                        "field": "capability",
                        "group": "explicit_icu",
                        "quote": "Level III NICU with ventilators",
                    }
                ],
            )
        )
        result = LlmEvidenceExtractor(client, config).extract(record)
        assert result.icu_subtypes == ["neonatal_icu"]
