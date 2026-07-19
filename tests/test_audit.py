"""Tests for the headline-metric audit (synthetic data only)."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from caregap_map.audit import (
    AUDIT_CATEGORIES,
    CAT_CLINIC,
    CAT_DENTIST,
    CAT_DIAGNOSTICS,
    CAT_DOCTOR,
    CAT_HOSPITAL,
    CAT_PHARMACY,
    CAT_UNKNOWN,
    audit_gap_records,
    audit_judgeability,
    audit_regional,
    audit_trusted_records,
    build_audit_report,
    categorize_for_audit,
    render_markdown,
)
from caregap_map.config import (
    CLASS_INSUFFICIENT,
    CLASS_LIKELY_GAP,
    CLASS_NEEDS_REVIEW,
    CLASS_TRUSTED,
    ScoringConfig,
)


class TestCategorizer:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("Astha Hospital", CAT_HOSPITAL),
            ("Rudrappa Nursing Home", CAT_HOSPITAL),
            ("Navjeevan Multispeciality Hospital", CAT_HOSPITAL),
            ("32 Gems Dental Clinic", CAT_DENTIST),
            ("City Dental Hospital", CAT_DENTIST),  # dental beats hospital
            ("Apollo Pharmacy", CAT_PHARMACY),
            ("Gupta Medical Store", CAT_PHARMACY),
            ("Aarthi Scans and Labs", CAT_DIAGNOSTICS),
            ("Eugene Molecular Diagnostics Centre", CAT_DIAGNOSTICS),
            ("Hypro Pathology Laboratory", CAT_DIAGNOSTICS),
            ("Om Clinic", CAT_CLINIC),
            ("Community Health Centre Baruipur", CAT_CLINIC),
            ("Dr. A. K. Sharma", CAT_DOCTOR),
            ("Dr Meena Verma", CAT_DOCTOR),
            # Dr prefix does not win over a stronger facility word.
            ("Dr. B. G Rudrappa Nursing Home", CAT_HOSPITAL),
            ("The Smile World", CAT_UNKNOWN),
            ("", CAT_UNKNOWN),
            (None, CAT_UNKNOWN),
        ],
    )
    def test_categories(self, name, expected):
        assert categorize_for_audit(name) == expected

    def test_organization_type_facility_is_ignored(self):
        # >99% of the supplied records carry the literal type "facility".
        assert categorize_for_audit("Some Name", "facility") == CAT_UNKNOWN

    def test_informative_organization_type_is_used(self):
        assert categorize_for_audit("Some Name", "pharmacy") == CAT_PHARMACY

    def test_hospital_and_diagnostics_prefers_hospital(self):
        assert categorize_for_audit("ABC Hospital & Diagnostics") == CAT_HOSPITAL

    def test_all_outputs_are_known_categories(self):
        for name in ("X Hospital", "Y Clinic", "Z Labs", "Q Pharmacy", "Dental Q", "Dr. Q", "Q"):
            assert categorize_for_audit(name) in AUDIT_CATEGORIES


def make_row(
    uid: str,
    classification: str = CLASS_LIKELY_GAP,
    name: str = "Test Hospital",
    completeness: int = 80,
    evidence: int = 0,
    comp_components: dict | None = None,
    fragments: list | None = None,
    state: str = "Kerala",
    district: str = "Ernakulam",
    coord_status: str = "ok",
    explicit: bool = False,
    subtypes: list | None = None,
    corroboration: list | None = None,
    flags: list | None = None,
    capacity: int | None = None,
    doctors: int | None = None,
    n_corr: int | None = None,
) -> dict:
    comp = comp_components if comp_components is not None else {
        "description": 20,
        "geography": 15,
        "source_url": 15,
        "procedure": 15,
        "equipment": 15,
    }
    corr = corroboration or []
    return {
        "unique_id": uid,
        "name": name,
        "organization_type": "facility",
        "classification": classification,
        "capability_evidence_score": evidence,
        "data_completeness_score": completeness,
        "explicit_icu_claim": explicit,
        "state_final": state,
        "district_final": district,
        "coord_status": coord_status,
        "capacity_int": capacity,
        "number_doctors_int": doctors,
        "n_corroboration_categories": n_corr if n_corr is not None else len(corr),
        "n_validation_flags": len(flags or []),
        "icu_subtypes_json": json.dumps(subtypes or []),
        "corroboration_categories_json": json.dumps(corr),
        "evidence_fragments_json": json.dumps(fragments or []),
        "validation_flags_json": json.dumps(flags or []),
        "evidence_components_json": json.dumps({"explicit_claim": 35} if explicit else {}),
        "completeness_components_json": json.dumps(comp),
    }


def make_scored(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


class TestJudgeabilityAudit:
    def test_counts_and_combinations(self):
        scored = make_scored(
            [
                make_row("a", comp_components={"description": 20, "geography": 15, "source_url": 15}),
                make_row("b", comp_components={"description": 20, "geography": 15, "source_url": 15}),
                make_row(
                    "c",
                    classification=CLASS_INSUFFICIENT,
                    completeness=20,
                    comp_components={"description": 20},
                ),
            ]
        )
        report = audit_judgeability(scored, ScoringConfig())
        assert report["records_total"] == 3
        assert report["records_judgeable"] == 2
        assert report["pct_judgeable_exact"] == pytest.approx(66.67, abs=0.01)
        top = report["component_combinations"][0]
        assert top["components"] == ["description", "geography", "source_url"]
        assert top["count"] == 2

    def test_solely_generated_detection(self):
        # description+procedure+equipment = 50 >= 45 but only 30 without them.
        generated_only = {"description": 20, "procedure": 15, "equipment": 15}
        # geography+source_url+capacity+staffing = 50 without generated fields.
        structural = {
            "geography": 15,
            "source_url": 15,
            "capacity": 10,
            "staffing": 10,
            "description": 20,
        }
        scored = make_scored(
            [
                make_row("a", comp_components=generated_only),
                make_row("b", comp_components=structural),
            ]
        )
        report = audit_judgeability(scored, ScoringConfig())
        assert report["among_judgeable"]["judgeable_solely_from_generated_fields"]["count"] == 1

    def test_only_generic_content_requires_populated_field_without_icu_fragment(self):
        icu_fragment = [
            {"field": "procedure", "group": "procedure", "pattern": "x", "text": "intensive care"}
        ]
        scored = make_scored(
            [
                make_row("generic"),  # procedure component present, no fragments
                make_row("icu", fragments=icu_fragment),
                make_row(
                    "no-fields",
                    comp_components={"description": 20, "geography": 15, "source_url": 15},
                ),
            ]
        )
        report = audit_judgeability(scored, ScoringConfig())
        assert report["among_judgeable"]["only_generic_procedure_equipment_content"]["count"] == 1
        assert report["among_judgeable"]["no_icu_relevant_procedure"]["count"] == 2

    def test_missing_field_counts(self):
        scored = make_scored(
            [
                make_row("a", coord_status="unparseable", district=None),
                make_row("b", capacity=120, doctors=5),
            ]
        )
        report = audit_judgeability(scored, ScoringConfig())
        among = report["among_judgeable"]
        assert among["missing_valid_coordinates"]["count"] == 1
        assert among["missing_resolved_district"]["count"] == 1
        # capacity/staffing components absent from both synthetic component dicts
        assert among["missing_capacity"]["count"] == 2

    def test_score_distribution_shape(self):
        scored = make_scored([make_row("a", completeness=100), make_row("b", completeness=50)])
        stats = audit_judgeability(scored, ScoringConfig())["completeness_score_distribution"]
        assert stats["count"] == 2
        assert stats["mean"] == 75.0
        assert stats["histogram"]["90-100"] == 1
        assert sum(stats["histogram"].values()) == 2


class TestGapAudit:
    def test_category_breakdown(self):
        scored = make_scored(
            [
                make_row("h", name="Sunrise Hospital"),
                make_row("d", name="Pearl Dental Clinic"),
                make_row("l", name="City Scans and Labs"),
                make_row("c", name="Om Clinic"),
                make_row("u", name="The Smile World"),
                make_row("t", classification=CLASS_TRUSTED, name="Other Hospital", evidence=80),
            ]
        )
        report = audit_gap_records(scored, ScoringConfig())
        assert report["gap_records_total"] == 5
        assert report["by_audit_category"]["hospital_like"] == 1
        assert report["by_audit_category"]["dentist"] == 1
        assert report["by_audit_category"]["diagnostics_or_lab"] == 1
        assert report["by_audit_category"]["clinic_or_health_center"] == 1
        assert report["by_audit_category"]["unknown"] == 1
        assert report["clearly_non_hospital"]["count"] == 2
        assert report["hospital_like"]["pct"] == 20.0
        assert report["facility_type_id_available"] is False

    def test_availability_counts(self):
        scored = make_scored(
            [
                make_row("a", capacity=50, doctors=3),
                make_row("b"),
            ]
        )
        report = audit_gap_records(scored, ScoringConfig())
        assert report["with_capacity"] == 1
        assert report["with_doctor_count"] == 1
        assert report["with_source_url"] == 2


class TestTrustedAudit:
    def _trusted_row(self, uid: str, evidence: int, corr: list, fragments: list, **kw) -> dict:
        return make_row(
            uid,
            classification=CLASS_TRUSTED,
            evidence=evidence,
            explicit=True,
            corroboration=corr,
            fragments=fragments,
            **kw,
        )

    def test_subtypes_corroboration_and_boundary(self):
        desc_frag = [{"field": "description", "group": "explicit_icu", "pattern": "x", "text": "ICU"}]
        cap_frag = [{"field": "capability", "group": "explicit_icu", "pattern": "x", "text": "ICU"}]
        scored = make_scored(
            [
                self._trusted_row(
                    "strong",
                    90,
                    ["equipment", "staffing", "bed_count"],
                    desc_frag,
                    subtypes=["general_or_unspecified"],
                ),
                self._trusted_row(
                    "barely",
                    45,
                    ["equipment", "procedure"],
                    cap_frag,
                    subtypes=["neonatal_icu"],
                ),
            ]
        )
        report = audit_trusted_records(scored, ScoringConfig())
        assert report["trusted_records_total"] == 2
        assert report["icu_subtype_distribution"]["neonatal_icu"] == 1
        assert report["corroboration_category_combinations"]["equipment + procedure"] == 1
        assert report["explicit_claim_never_in_description"] == 1
        boundary_ids = [r["unique_id"] for r in report["boundary_cases"]["records"]]
        # "barely" qualifies twice over (score and min corroboration);
        # "strong" has 3 categories and a high score.
        assert boundary_ids == ["barely"]

    def test_empty_trusted_set(self):
        scored = make_scored([make_row("a")])
        assert audit_trusted_records(scored, ScoringConfig()) == {"trusted_records_total": 0}


class TestRegionalAudit:
    def test_single_trusted_and_dominance(self):
        # District A: one trusted record among 3 -> single-record trusted status.
        # District B: 3 dental records -> dominated by non-hospital, no trust.
        rows = [
            make_row(
                "t1",
                classification=CLASS_TRUSTED,
                name="Big Hospital",
                evidence=80,
                district="A",
            ),
            make_row("a2", district="A", name="Some Hospital"),
            make_row("a3", district="A", name="Other Hospital"),
            make_row("b1", district="B", name="Dental One"),
            make_row("b2", district="B", name="Dental Two"),
            make_row("b3", district="B", name="Dental Three"),
        ]
        report = audit_regional(make_scored(rows), ScoringConfig())
        assert report["districts_total"] == 2
        single = report["districts_where_one_trusted_record_decides_status"]
        assert single["count"] == 1
        assert single["districts"] == ["Kerala / A"]
        dominated = report["districts_dominated_by_non_hospital_records"]
        assert dominated["count"] == 1
        assert "Kerala / B" in dominated["top20"]

    def test_status_counts_add_up(self):
        rows = [make_row(f"r{i}", district=f"D{i}") for i in range(4)]
        report = audit_regional(make_scored(rows), ScoringConfig())
        assert sum(report["district_status_counts"].values()) == report["districts_total"]


class TestFullReport:
    def test_json_serializable_and_rendered(self):
        scored = make_scored(
            [
                make_row(
                    "t",
                    classification=CLASS_TRUSTED,
                    evidence=80,
                    explicit=True,
                    corroboration=["equipment", "staffing"],
                    fragments=[
                        {"field": "description", "group": "explicit_icu", "pattern": "x", "text": "ICU"}
                    ],
                    subtypes=["general_or_unspecified"],
                ),
                make_row("g", name="Pearl Dental"),
                make_row("r", classification=CLASS_NEEDS_REVIEW, evidence=30),
                make_row(
                    "i",
                    classification=CLASS_INSUFFICIENT,
                    completeness=20,
                    comp_components={"description": 20},
                ),
            ]
        )
        report = build_audit_report(scored, ScoringConfig())
        serialized = json.dumps(report)
        assert "judgeability" in serialized
        md = render_markdown(report)
        assert "Headline-metric audit" in md
        assert "dataset-evidence analysis" in md
        assert report["classification_counts"][CLASS_TRUSTED] == 1
