"""Evidence extraction: signals, fragments, negation, bed counts."""

import json

from caregap_map.evidence import extract_evidence
from conftest import make_record


class TestStrongEvidence:
    def test_all_signal_groups_fire(self, config):
        record = make_record(
            description=(
                "Tertiary hospital with a 20-bed intensive care unit. "
                "Ventilators and defibrillators are available. "
                "The ICU is staffed by an intensivist around the clock."
            ),
            equipment=json.dumps(["Ventilator x 10", "Defibrillator"]),
            procedure=json.dumps(["Mechanical ventilation", "Emergency resuscitation"]),
        )
        ev = extract_evidence(record, config)
        assert ev.explicit_icu_claim
        assert ev.equipment_signals
        assert ev.procedure_signals
        assert ev.staffing_signals
        assert ev.icu_bed_count == 20
        assert ev.capacity_signal
        assert not ev.contradiction_flags

    def test_fragments_keep_exact_original_text(self, config):
        record = make_record(capability=json.dumps(["22-bed Level II Intensive Care Unit (ICU)"]))
        ev = extract_evidence(record, config)
        texts = [f.text for f in ev.supporting_text_fragments]
        assert "22-bed Level II Intensive Care Unit (ICU)" in texts

    def test_fragments_deduplicated_per_field_group(self, config):
        # "Intensive Care Unit (ICU)" matches two explicit patterns; the
        # reviewer should still see the sentence once.
        record = make_record(capability=json.dumps(["Intensive Care Unit (ICU) available"]))
        ev = extract_evidence(record, config)
        explicit = [f for f in ev.supporting_text_fragments if f.group == "explicit_icu"]
        assert len(explicit) == 1


class TestNoEvidence:
    def test_non_icu_facility_has_no_signals(self, config):
        ev = extract_evidence(make_record(), config)
        assert not ev.explicit_icu_claim
        assert not ev.equipment_signals
        assert ev.icu_bed_count is None
        assert "no explicit ICU / intensive-care claim" in ev.missing_evidence

    def test_null_like_placeholders_are_not_evidence(self, config):
        record = make_record(
            description="null",
            capability="[]",
            specialties="[]",
            procedure="[]",
            equipment="[]",
        )
        ev = extract_evidence(record, config)
        assert not ev.explicit_icu_claim
        assert not ev.supporting_text_fragments
        assert "no description text" in ev.missing_evidence


class TestNegation:
    def test_negated_icu_is_contradiction_not_evidence(self, config):
        record = make_record(
            description="The facility has no ICU and refers critical patients elsewhere.",
            capability=json.dumps(["ICU services"]),
        )
        ev = extract_evidence(record, config)
        assert "negated_icu_mention" in ev.contradiction_flags
        # The claim from the capability field still registers ...
        assert ev.explicit_icu_claim
        # ... but the negated sentence contributed no positive fragment.
        desc_positive = [
            f for f in ev.supporting_text_fragments if f.field == "description" and f.group != "negation"
        ]
        assert not desc_positive

    def test_negation_fragment_retained_for_reviewer(self, config):
        record = make_record(description="There is no intensive care unit on site.")
        ev = extract_evidence(record, config)
        neg = [f for f in ev.supporting_text_fragments if f.group == "negation"]
        assert neg and "no intensive care" in neg[0].text.lower()


class TestSuspiciousClaims:
    def test_icu_beds_exceeding_capacity(self, config):
        record = make_record(
            description="Hospital with a 50-bed ICU.",
            capacity="20",
        )
        ev = extract_evidence(record, config)
        assert ev.icu_bed_count == 50
        assert "icu_beds_exceed_total_capacity" in ev.suspicious_claim_flags

    def test_consistent_bed_count_is_not_suspicious(self, config):
        record = make_record(description="Hospital with a 10-bed ICU.", capacity="200")
        ev = extract_evidence(record, config)
        assert not ev.suspicious_claim_flags
