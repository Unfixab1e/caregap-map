"""Metrics for the human-labelled evaluation set.

Agreement with the deterministic baseline is diagnostic only; these metrics
compare BOTH extractors against human ground-truth labels. False Trusted and
false Likely-Gap findings matter more than aggregate accuracy: a false
Trusted hides a real gap, a false Gap manufactures a desert.
"""

from __future__ import annotations

import pandas as pd

from .config import CLASS_LIKELY_GAP, CLASS_TRUSTED

# Columns a labelled evaluation file must provide (see evals/icu_review_template.csv).
REQUIRED_COLUMNS = [
    "unique_id",
    "current_classification",
    "llm_classification",
    "human_expected_classification",
    "explicit_icu_claim",
    "corroborated",
    "subtype",
    "judgeable",
    "false_trusted_risk",
    "false_gap_risk",
    "reviewer_rationale",
    "reviewed_by",
    "review_timestamp",
]


def _confusion(labels: pd.Series, predictions: pd.Series) -> dict:
    table = pd.crosstab(labels, predictions, dropna=False)
    return {str(human): {str(k): int(v) for k, v in row.items()} for human, row in table.iterrows()}


def _per_class_metrics(labels: pd.Series, predictions: pd.Series) -> dict:
    out = {}
    for cls in sorted(set(labels) | set(predictions)):
        tp = int(((labels == cls) & (predictions == cls)).sum())
        fp = int(((labels != cls) & (predictions == cls)).sum())
        fn = int(((labels == cls) & (predictions != cls)).sum())
        out[cls] = {
            "precision": round(tp / (tp + fp), 3) if tp + fp else None,
            "recall": round(tp / (tp + fn), 3) if tp + fn else None,
            "support": tp + fn,
        }
    return out


def evaluate_labels(df: pd.DataFrame) -> dict:
    """Compute the evaluation report from a (partially) labelled review file.

    Rows without ``human_expected_classification`` are counted as pending and
    excluded from metrics. Returns a JSON-serialisable dict.
    """
    total = len(df)
    labelled = df[
        df["human_expected_classification"].notna()
        & (df["human_expected_classification"].astype(str).str.strip() != "")
    ].copy()
    report: dict = {
        "rows_total": total,
        "rows_labelled": len(labelled),
        "rows_pending": total - len(labelled),
    }
    if labelled.empty:
        report["note"] = "No human labels yet - fill human_expected_classification first."
        return report

    human = labelled["human_expected_classification"].astype(str).str.strip()
    det = labelled["current_classification"].astype(str).str.strip()
    report["deterministic"] = {
        "agreement_pct": round(100.0 * (det == human).mean(), 1),
        "confusion_matrix_human_rows": _confusion(human, det),
        "per_class": _per_class_metrics(human, det),
        # The failure modes that matter most:
        "false_trusted": int(((det == CLASS_TRUSTED) & (human != CLASS_TRUSTED)).sum()),
        "false_gap": int(((det == CLASS_LIKELY_GAP) & (human != CLASS_LIKELY_GAP)).sum()),
    }

    has_llm = labelled["llm_classification"].notna() & (
        labelled["llm_classification"].astype(str).str.strip() != ""
    )
    llm_rows = labelled[has_llm]
    if len(llm_rows):
        llm = llm_rows["llm_classification"].astype(str).str.strip()
        llm_human = llm_rows["human_expected_classification"].astype(str).str.strip()
        report["llm_assisted"] = {
            "rows": len(llm_rows),
            "agreement_pct": round(100.0 * (llm == llm_human).mean(), 1),
            "confusion_matrix_human_rows": _confusion(llm_human, llm),
            "false_trusted": int(((llm == CLASS_TRUSTED) & (llm_human != CLASS_TRUSTED)).sum()),
            "false_gap": int(((llm == CLASS_LIKELY_GAP) & (llm_human != CLASS_LIKELY_GAP)).sum()),
        }

    # Subtype-specific errors: reviewer recorded a subtype differing from the
    # extractor's (free-text comparison, case-insensitive).
    if "subtype" in labelled.columns and "extractor_subtypes" in labelled.columns:
        sub = labelled[
            labelled["subtype"].notna() & (labelled["subtype"].astype(str).str.strip() != "")
        ]
        mismatches = [
            {
                "unique_id": r["unique_id"],
                "reviewer_subtype": r["subtype"],
                "extractor_subtypes": r["extractor_subtypes"],
            }
            for _, r in sub.iterrows()
            if str(r["subtype"]).strip().lower()
            not in str(r.get("extractor_subtypes", "")).lower()
        ]
        report["subtype_mismatches"] = mismatches

    report["disagreements_requiring_review"] = [
        {
            "unique_id": r["unique_id"],
            "human": r["human_expected_classification"],
            "deterministic": r["current_classification"],
            "llm": r.get("llm_classification"),
            "rationale": r.get("reviewer_rationale"),
        }
        for _, r in labelled.iterrows()
        if str(r["current_classification"]).strip()
        != str(r["human_expected_classification"]).strip()
    ]
    return report
