"""Bed-count anchoring: number + bed + ICU context must co-occur in one passage."""

import json

import pytest

from caregap_map.evidence import extract_icu_bed_count
from conftest import make_record


class TestDeterministicAnchoring:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("10-bed ICU", 10),
            ("ICU has 10 beds", 10),
            ("The hospital runs a 12 bedded ICU.", 12),
            ("20 - bed ICU on the second floor", 20),
            ("Critical care unit with 8 beds", 8),
            ("6-bed intensive care unit", 6),
            ("6   ICU beds", 6),
        ],
    )
    def test_valid_anchored_counts(self, text, expected, config):
        assert extract_icu_bed_count([text], config) == expected

    @pytest.mark.parametrize(
        "text",
        [
            "10 ventilators; ICU available",
            "Hospital has 100 beds and an ICU",
            "100 beds. ICU available.",
            "ICU available",
            "10 beds",  # bed count with no ICU context at all
        ],
    )
    def test_unanchored_numbers_rejected(self, text, config):
        assert extract_icu_bed_count([text], config) is None

    def test_number_in_unrelated_passage_rejected(self, config):
        # Number and ICU context in DIFFERENT passages never combine.
        assert extract_icu_bed_count(["10 ventilators", "ICU available"], config) is None

    def test_largest_anchored_count_wins(self, config):
        texts = ["6-bed ICU in the old wing", "new 14-bed ICU commissioned"]
        assert extract_icu_bed_count(texts, config) == 14


class TestLlmBedCountAnchoring:
    """The model's reported count must be re-derivable from verified fragments."""

    def _extract(self, record, payload, config):
        from caregap_map.llm_extraction import LlmEvidenceExtractor
        from test_llm_extraction import StubClient, llm_payload

        client = StubClient(llm_payload(**payload))
        return LlmEvidenceExtractor(client, config).extract(record)

    def test_anchored_count_accepted(self, config):
        record = make_record(description="Runs a 10-bed ICU with monitors.")
        result = self._extract(
            record,
            dict(
                explicit_icu_claim=True,
                icu_bed_count=10,
                fragments=[{"field": "description", "group": "explicit_icu", "quote": "10-bed ICU"}],
            ),
            config,
        )
        assert result.icu_bed_count == 10
        assert "llm_bed_count_unanchored" not in result.suspicious_claim_flags

    def test_cooccurrence_across_fragments_rejected(self, config):
        # The exact risk case: "10 ventilators" + "ICU available" fragments.
        record = make_record(
            description="ICU available on site.",
            equipment=json.dumps(["10 ventilators"]),
        )
        result = self._extract(
            record,
            dict(
                explicit_icu_claim=True,
                icu_bed_count=10,
                fragments=[
                    {"field": "description", "group": "explicit_icu", "quote": "ICU available"},
                    {"field": "equipment", "group": "equipment", "quote": "10 ventilators"},
                ],
            ),
            config,
        )
        assert result.icu_bed_count is None
        assert "llm_bed_count_unanchored" in result.suspicious_claim_flags

    def test_total_hospital_beds_rejected(self, config):
        record = make_record(description="Hospital has 100 beds and an ICU.")
        result = self._extract(
            record,
            dict(
                explicit_icu_claim=True,
                icu_bed_count=100,
                fragments=[
                    {
                        "field": "description",
                        "group": "explicit_icu",
                        "quote": "Hospital has 100 beds and an ICU",
                    }
                ],
            ),
            config,
        )
        assert result.icu_bed_count is None
        assert "llm_bed_count_unanchored" in result.suspicious_claim_flags

    def test_payload_mismatch_flagged_but_anchored_value_wins(self, config):
        record = make_record(description="Runs a 10-bed ICU.")
        result = self._extract(
            record,
            dict(
                explicit_icu_claim=True,
                icu_bed_count=99,  # model exaggerates; source says 10
                fragments=[{"field": "description", "group": "explicit_icu", "quote": "10-bed ICU"}],
            ),
            config,
        )
        assert result.icu_bed_count == 10
        assert "llm_bed_count_mismatch" in result.suspicious_claim_flags
