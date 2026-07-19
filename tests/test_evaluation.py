"""Evaluation metrics: false Trusted / false Gap are first-class outputs."""

import pandas as pd

from caregap_map.config import (
    CLASS_INSUFFICIENT,
    CLASS_LIKELY_GAP,
    CLASS_NEEDS_REVIEW,
    CLASS_TRUSTED,
)
from caregap_map.evaluation import evaluate_labels


def row(uid, det, llm, human, subtype="", extractor_subtypes=""):
    return {
        "unique_id": uid,
        "current_classification": det,
        "llm_classification": llm,
        "human_expected_classification": human,
        "explicit_icu_claim": "",
        "corroborated": "",
        "subtype": subtype,
        "extractor_subtypes": extractor_subtypes,
        "judgeable": "",
        "false_trusted_risk": "",
        "false_gap_risk": "",
        "reviewer_rationale": "r",
        "reviewed_by": "tester",
        "review_timestamp": "2026-07-18",
    }


class TestEvaluateLabels:
    def test_unlabelled_file_reports_pending(self):
        df = pd.DataFrame([row("a", CLASS_TRUSTED, "", "")])
        report = evaluate_labels(df)
        assert report["rows_labelled"] == 0
        assert report["rows_pending"] == 1
        assert "deterministic" not in report

    def test_false_trusted_and_false_gap_counted(self):
        df = pd.DataFrame(
            [
                # det says Trusted, human says Needs Review -> false trusted
                row("a", CLASS_TRUSTED, CLASS_NEEDS_REVIEW, CLASS_NEEDS_REVIEW),
                # det says Gap, human says Insufficient -> false gap
                row("b", CLASS_LIKELY_GAP, CLASS_LIKELY_GAP, CLASS_INSUFFICIENT),
                # agreement
                row("c", CLASS_TRUSTED, CLASS_TRUSTED, CLASS_TRUSTED),
            ]
        )
        report = evaluate_labels(df)
        det = report["deterministic"]
        assert det["false_trusted"] == 1
        assert det["false_gap"] == 1
        assert det["agreement_pct"] == 33.3
        llm = report["llm_assisted"]
        assert llm["false_trusted"] == 0
        assert llm["false_gap"] == 1  # llm also called "b" a gap

    def test_confusion_and_per_class(self):
        df = pd.DataFrame(
            [
                row("a", CLASS_TRUSTED, "", CLASS_TRUSTED),
                row("b", CLASS_TRUSTED, "", CLASS_NEEDS_REVIEW),
            ]
        )
        det = evaluate_labels(df)["deterministic"]
        assert det["confusion_matrix_human_rows"][CLASS_TRUSTED][CLASS_TRUSTED] == 1
        assert det["per_class"][CLASS_TRUSTED]["precision"] == 0.5
        assert det["per_class"][CLASS_TRUSTED]["recall"] == 1.0

    def test_subtype_mismatches_surfaced(self):
        df = pd.DataFrame(
            [
                row(
                    "a",
                    CLASS_TRUSTED,
                    "",
                    CLASS_TRUSTED,
                    subtype="neonatal_icu",
                    extractor_subtypes='["general_or_unspecified"]',
                )
            ]
        )
        report = evaluate_labels(df)
        assert report["subtype_mismatches"][0]["unique_id"] == "a"

    def test_disagreements_listed_with_rationale(self):
        df = pd.DataFrame([row("a", CLASS_TRUSTED, "", CLASS_NEEDS_REVIEW)])
        report = evaluate_labels(df)
        d = report["disagreements_requiring_review"][0]
        assert d["unique_id"] == "a" and d["rationale"] == "r"

    def test_codex_assisted_agreement_reported_when_present(self):
        rows = [
            row("a", CLASS_TRUSTED, "", CLASS_TRUSTED),
            row("b", CLASS_LIKELY_GAP, "", CLASS_INSUFFICIENT),
        ]
        rows[0]["codex_classification"] = CLASS_TRUSTED
        rows[1]["codex_classification"] = CLASS_LIKELY_GAP  # false gap vs human
        report = evaluate_labels(pd.DataFrame(rows))
        codex = report["codex_assisted"]
        assert codex["rows"] == 2
        assert codex["agreement_pct"] == 50.0
        assert codex["false_gap"] == 1
        assert codex["false_trusted"] == 0

    def test_codex_absent_column_is_fine(self):
        report = evaluate_labels(pd.DataFrame([row("a", CLASS_TRUSTED, "", CLASS_TRUSTED)]))
        assert "codex_assisted" not in report

    def test_by_audit_category_error_breakdown(self):
        rows = [
            row("a", CLASS_LIKELY_GAP, "", CLASS_INSUFFICIENT),  # false gap on a dentist
            row("b", CLASS_LIKELY_GAP, "", CLASS_LIKELY_GAP),
            row("c", CLASS_TRUSTED, "", CLASS_TRUSTED),
        ]
        rows[0]["audit_category"] = "dentist"
        rows[1]["audit_category"] = "dentist"
        rows[2]["audit_category"] = "hospital_like"
        report = evaluate_labels(pd.DataFrame(rows))
        by_cat = report["by_audit_category"]
        assert by_cat["dentist"]["rows"] == 2
        assert by_cat["dentist"]["false_gap"] == 1
        assert by_cat["dentist"]["deterministic_agreement_pct"] == 50.0
        assert by_cat["hospital_like"]["false_gap"] == 0
