"""LLM extractor: fragment verification, claim anchoring, deterministic validation."""

import json

import pytest

from caregap_map.config import CLASS_NEEDS_REVIEW
from caregap_map.llm_extraction import (
    LlmEvidenceExtractor,
    LlmExtractionError,
    build_user_prompt,
    locate_fragment,
)
from caregap_map.scoring import score_facility
from conftest import make_record


class StubClient:
    """Returns a canned payload; records whether it was called."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def complete_json(self, system, user, schema, config):
        self.calls += 1
        return self.payload if isinstance(self.payload, str) else json.dumps(self.payload)


def llm_payload(**overrides) -> dict:
    payload = {
        "explicit_icu_claim": False,
        "icu_bed_count": None,
        "fragments": [],
        "unclear_claims": [],
        "explanation": "test",
    }
    payload.update(overrides)
    return payload


class TestLocateFragment:
    def test_exact_match_returns_source_substring(self):
        source = "Hospital with a 20-bed ICU and two theatres."
        assert locate_fragment("20-bed ICU", source) == "20-bed ICU"

    def test_whitespace_differences_tolerated(self):
        source = "Intensive  care\nunit available"
        located = locate_fragment("Intensive care unit", source)
        assert located == "Intensive  care\nunit"  # the SOURCE's exact text wins

    def test_paraphrase_rejected(self):
        assert locate_fragment("has an ICU ward", "Intensive care unit available") is None

    def test_empty_quote_rejected(self):
        assert locate_fragment("   ", "anything") is None


class TestFragmentVerification:
    def test_verified_fragment_accepted(self, config):
        record = make_record(capability=json.dumps(["24x7 ICU with ventilator support"]))
        client = StubClient(
            llm_payload(
                explicit_icu_claim=True,
                fragments=[
                    {
                        "field": "capability",
                        "group": "explicit_icu",
                        "quote": "24x7 ICU with ventilator support",
                    }
                ],
            )
        )
        result = LlmEvidenceExtractor(client, config).extract(record)
        assert result.extractor == "llm"
        assert result.explicit_icu_claim
        assert result.supporting_text_fragments[0].text == "24x7 ICU with ventilator support"
        assert not result.suspicious_claim_flags

    def test_hallucinated_fragment_dropped_and_flagged(self, config):
        record = make_record()  # no ICU text anywhere
        client = StubClient(
            llm_payload(
                explicit_icu_claim=True,
                fragments=[
                    {"field": "description", "group": "explicit_icu", "quote": "state-of-the-art ICU"}
                ],
            )
        )
        result = LlmEvidenceExtractor(client, config).extract(record)
        assert result.supporting_text_fragments == []
        assert "llm_unverified_fragments_dropped:1" in result.suspicious_claim_flags
        # The claim is NOT honoured without a verified fragment behind it.
        assert not result.explicit_icu_claim

    def test_bed_count_needs_a_verified_fragment(self, config):
        record = make_record(description="General hospital with a 10-bed ICU on site.")
        with_fragment = StubClient(
            llm_payload(
                explicit_icu_claim=True,
                icu_bed_count=10,
                fragments=[{"field": "description", "group": "explicit_icu", "quote": "10-bed ICU"}],
            )
        )
        assert LlmEvidenceExtractor(with_fragment, config).extract(record).icu_bed_count == 10

        without_fragment = StubClient(llm_payload(icu_bed_count=99))
        assert LlmEvidenceExtractor(without_fragment, config).extract(record).icu_bed_count is None

    def test_negation_becomes_contradiction(self, config):
        record = make_record(description="The facility has no ICU and refers patients out.")
        client = StubClient(
            llm_payload(fragments=[{"field": "description", "group": "negation", "quote": "has no ICU"}])
        )
        result = LlmEvidenceExtractor(client, config).extract(record)
        assert "negated_icu_mention" in result.contradiction_flags

    def test_unclear_claims_and_explanation_surface(self, config):
        record = make_record()
        client = StubClient(
            llm_payload(unclear_claims=["'advanced care' is vague"], explanation="mostly OPD text")
        )
        result = LlmEvidenceExtractor(client, config).extract(record)
        assert result.unclear_claims == ["'advanced care' is vague"]
        assert result.extraction_explanation == "mostly OPD text"


class TestDeterministicGuardrails:
    def test_validation_still_applies_to_llm_evidence(self, config):
        # LLM verifies an explicit claim but offers no corroboration:
        # the deterministic validator must still demote it to review.
        record = make_record(capability=json.dumps(["ICU available"]))
        client = StubClient(
            llm_payload(
                explicit_icu_claim=True,
                fragments=[{"field": "capability", "group": "explicit_icu", "quote": "ICU available"}],
            )
        )
        extractor = LlmEvidenceExtractor(client, config)
        s = score_facility(record, config, extractor=extractor.extract)
        assert s.classification == CLASS_NEEDS_REVIEW
        assert any(f.name == "icu_claim_uncorroborated" for f in s.validation_flags)

    def test_consistency_checks_run_on_llm_results(self, config):
        record = make_record(
            description="Advertises a 50-bed ICU.",
            capacity="20",
        )
        client = StubClient(
            llm_payload(
                explicit_icu_claim=True,
                icu_bed_count=50,
                fragments=[{"field": "description", "group": "explicit_icu", "quote": "50-bed ICU"}],
            )
        )
        result = LlmEvidenceExtractor(client, config).extract(record)
        assert "icu_beds_exceed_total_capacity" in result.suspicious_claim_flags

    def test_empty_record_skips_the_api_call(self, config):
        record = make_record(
            description=None, capability="[]", specialties="[]", procedure="[]", equipment="[]"
        )
        client = StubClient(llm_payload())
        result = LlmEvidenceExtractor(client, config).extract(record)
        assert client.calls == 0
        assert result.extractor == "llm"
        assert "no description text" in result.missing_evidence

    def test_invalid_json_raises(self, config):
        client = StubClient("this is not json {")
        with pytest.raises(LlmExtractionError):
            LlmEvidenceExtractor(client, config).extract(make_record())


class TestPrompt:
    def test_prompt_contains_field_texts(self):
        record = make_record(description="Unique marker sentence for the prompt.")
        from caregap_map.evidence import field_texts

        prompt = build_user_prompt(field_texts(record))
        assert "Unique marker sentence for the prompt." in prompt
        assert "[description]" in prompt
