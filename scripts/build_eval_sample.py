"""Build/extend the human-review evaluation sample (evals/private/icu_review.csv).

Stratified across:

- the four classifications (15 Trusted / 10 Needs Review / 10 Likely Gap /
  5 Insufficient);
- every deterministic-vs-LLM disagreement (and, with --codex-parquet,
  every deterministic-vs-Codex disagreement from a STABLE snapshot);
- specialised ICU subtypes (up to 3 records per subtype);
- audit categories among the gap bucket (up to 5 each: hospital-like,
  clinic/health centre, pharmacy, dentist, individual doctor,
  diagnostics/lab) so non-hospital "no ICU evidence" semantics get
  human labels.

MERGE-PRESERVING: if the output file already exists, all its rows -
including any human labels - are kept untouched and only new unique_ids
are appended. The output is GIT-IGNORED (real IDs + source excerpts).

    python scripts/build_eval_sample.py [--data-dir data] [--out evals/private/icu_review.csv]
                                        [--codex-parquet <stable snapshot>]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from caregap_map.audit import CAT_UNKNOWN, categorize_for_audit  # noqa: E402
from caregap_map.config import (  # noqa: E402
    CLASS_INSUFFICIENT,
    CLASS_LIKELY_GAP,
    CLASS_NEEDS_REVIEW,
    CLASS_TRUSTED,
    DataPaths,
)
from caregap_map.evaluation import REQUIRED_COLUMNS  # noqa: E402

STRATA = {CLASS_TRUSTED: 15, CLASS_NEEDS_REVIEW: 10, CLASS_LIKELY_GAP: 10, CLASS_INSUFFICIENT: 5}
N_PER_SUBTYPE = 3
N_PER_AUDIT_CATEGORY = 5  # among the gap bucket, per non-"unknown" category

SUBTYPES = ["neonatal_icu", "pediatric_icu", "cardiac_icu", "medical_icu", "surgical_icu"]


def excerpt(row: pd.Series) -> str:
    """Short evidence excerpt for the labeller: fragments first, else description."""
    fragments = json.loads(row.get("evidence_fragments_json") or "[]")
    texts = list(dict.fromkeys(f["text"] for f in fragments))[:4]
    if not texts and isinstance(row.get("description"), str):
        texts = [row["description"][:300]]
    return " | ".join(t[:160] for t in texts)


def build_sample(
    scored: pd.DataFrame,
    llm_by_id: dict[str, str],
    codex_by_id: dict[str, str],
) -> pd.DataFrame:
    """Deterministically select the stratified review candidates."""
    picked: list[pd.DataFrame] = []
    for cls, n in STRATA.items():
        picked.append(scored[scored["classification"] == cls].sort_values("unique_id").head(n))

    # Every det-vs-model disagreement (LLM always; Codex from a stable snapshot).
    for by_id in (llm_by_id, codex_by_id):
        ids = [uid for uid, cls in by_id.items() if isinstance(cls, str) and cls]
        dis = scored[scored["unique_id"].isin(ids)]
        dis = dis[dis["classification"] != dis["unique_id"].map(by_id)]
        picked.append(dis)

    # Specialised-subtype diversity: up to N per subtype.
    subtype_lists = scored["icu_subtypes_json"].fillna("[]").map(json.loads)
    for subtype in SUBTYPES:
        mask = subtype_lists.map(lambda subs, s=subtype: s in subs)
        picked.append(scored[mask].sort_values("unique_id").head(N_PER_SUBTYPE))

    # Audit-category diversity among the gap bucket, so non-hospital
    # absence semantics (pharmacy/dentist/lab/clinic) get human labels.
    gaps = scored[scored["classification"] == CLASS_LIKELY_GAP]
    categories = gaps.apply(
        lambda r: categorize_for_audit(r.get("name"), r.get("organization_type")), axis=1
    )
    for category in sorted(categories.unique()):
        if category == CAT_UNKNOWN:
            continue
        picked.append(
            gaps[categories == category].sort_values("unique_id").head(N_PER_AUDIT_CATEGORY)
        )

    sample = pd.concat(picked).drop_duplicates("unique_id")
    out = pd.DataFrame(
        {
            "unique_id": sample["unique_id"],
            "name": sample["name"],
            "audit_category": sample.apply(
                lambda r: categorize_for_audit(r.get("name"), r.get("organization_type")), axis=1
            ),
            "current_classification": sample["classification"],
            "llm_classification": sample["unique_id"].map(llm_by_id).fillna(""),
            "codex_classification": sample["unique_id"].map(codex_by_id).fillna(""),
            "extractor_subtypes": sample["icu_subtypes_json"].fillna("[]"),
            "evidence_excerpt": sample.apply(excerpt, axis=1),
        }
    )
    for col in REQUIRED_COLUMNS:
        if col not in out.columns:
            out[col] = ""
    return out


def merge_preserving(existing: pd.DataFrame, new: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Keep every existing row (labels included); append only new unique_ids."""
    existing = existing.copy()
    for col in new.columns:
        if col not in existing.columns:
            existing[col] = ""
    additions = new[~new["unique_id"].isin(existing["unique_id"])]
    return pd.concat([existing, additions], ignore_index=True), len(additions)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--out", default="evals/private/icu_review.csv")
    parser.add_argument(
        "--codex-parquet",
        default=None,
        help="STABLE snapshot of Codex-scored facilities (never point this at a running "
        "batch-extraction output directory).",
    )
    args = parser.parse_args()

    paths = DataPaths(data_dir=Path(args.data_dir))
    if not paths.facilities_scored_parquet.exists():
        print("ERROR: run scripts/build_processed_data.py first.", file=sys.stderr)
        return 1
    scored = pd.read_parquet(paths.facilities_scored_parquet)

    def classification_map(path: Path, column: str) -> dict[str, str]:
        if not path.exists():
            return {}
        df = pd.read_parquet(path)
        if column not in df.columns:
            return {}
        return dict(zip(df["unique_id"], df[column].fillna(""), strict=True))

    llm_by_id = classification_map(
        paths.processed_dir / "facilities_scored_llm.parquet", "llm_classification"
    )
    codex_by_id = (
        classification_map(Path(args.codex_parquet), "codex_classification")
        if args.codex_parquet
        else {}
    )

    new = build_sample(scored, llm_by_id, codex_by_id)

    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    appended = len(new)
    if dest.exists():
        existing = pd.read_csv(dest, dtype=str).fillna("")
        merged, appended = merge_preserving(existing, new)
    else:
        merged = new
    merged.to_csv(dest, index=False)

    print(f"Wrote {len(merged)} rows to {dest} (git-ignored); {appended} newly appended.")
    print("Class distribution:")
    print(merged["current_classification"].value_counts().to_string())
    if "audit_category" in merged.columns:
        print("\nAudit-category distribution:")
        print(merged["audit_category"].replace("", "(pre-existing row)").value_counts().to_string())
    print("\nNext: fill the human_* columns, then run scripts/evaluate_labels.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
