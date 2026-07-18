"""Build the human-review evaluation sample (evals/private/icu_review.csv).

Stratified: 15 Trusted / 10 Needs Review / 10 Likely Gap / 5 Insufficient,
plus all deterministic-vs-LLM disagreements and specialised-subtype records.
The output is GIT-IGNORED (real IDs + source excerpts). Usage:

    python scripts/build_eval_sample.py [--data-dir data]
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

from caregap_map.config import (  # noqa: E402
    CLASS_INSUFFICIENT,
    CLASS_LIKELY_GAP,
    CLASS_NEEDS_REVIEW,
    CLASS_TRUSTED,
    DataPaths,
)
from caregap_map.evaluation import REQUIRED_COLUMNS  # noqa: E402

STRATA = {CLASS_TRUSTED: 15, CLASS_NEEDS_REVIEW: 10, CLASS_LIKELY_GAP: 10, CLASS_INSUFFICIENT: 5}
N_SUBTYPE_EXTRA = 6  # specialised-subtype records added for diversity


def excerpt(row: pd.Series) -> str:
    """Short evidence excerpt for the labeller: fragments first, else description."""
    fragments = json.loads(row.get("evidence_fragments_json") or "[]")
    texts = list(dict.fromkeys(f["text"] for f in fragments))[:4]
    if not texts and isinstance(row.get("description"), str):
        texts = [row["description"][:300]]
    return " | ".join(t[:160] for t in texts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data")
    args = parser.parse_args()

    paths = DataPaths(data_dir=Path(args.data_dir))
    if not paths.facilities_scored_parquet.exists():
        print("ERROR: run scripts/build_processed_data.py first.", file=sys.stderr)
        return 1
    scored = pd.read_parquet(paths.facilities_scored_parquet)

    llm_by_id: dict[str, str] = {}
    llm_path = paths.processed_dir / "facilities_scored_llm.parquet"
    if llm_path.exists():
        llm = pd.read_parquet(llm_path)
        llm_by_id = dict(zip(llm["unique_id"], llm.get("llm_classification", ""), strict=True))

    picked: list[pd.DataFrame] = []
    for cls, n in STRATA.items():
        picked.append(scored[scored["classification"] == cls].sort_values("unique_id").head(n))
    # Every det-vs-LLM disagreement.
    disagree_ids = [uid for uid, llm_cls in llm_by_id.items() if isinstance(llm_cls, str) and llm_cls]
    dis = scored[scored["unique_id"].isin(disagree_ids)]
    dis = dis[dis["classification"] != dis["unique_id"].map(llm_by_id)]
    picked.append(dis)
    # Specialised-subtype diversity (records whose subtypes exclude general).
    subtyped = scored[
        scored["icu_subtypes_json"]
        .fillna("[]")
        .apply(lambda s: bool(json.loads(s)) and "general_or_unspecified" not in json.loads(s))
    ]
    picked.append(subtyped.sort_values("unique_id").head(N_SUBTYPE_EXTRA))

    sample = pd.concat(picked).drop_duplicates("unique_id")

    out = pd.DataFrame(
        {
            "unique_id": sample["unique_id"],
            "name": sample["name"],
            "current_classification": sample["classification"],
            "llm_classification": sample["unique_id"].map(llm_by_id).fillna(""),
            "extractor_subtypes": sample["icu_subtypes_json"].fillna("[]"),
            "evidence_excerpt": sample.apply(excerpt, axis=1),
        }
    )
    for col in REQUIRED_COLUMNS:
        if col not in out.columns:
            out[col] = ""

    dest = Path("evals/private/icu_review.csv")
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(dest, index=False)
    print(f"Wrote {len(out)} rows to {dest} (git-ignored).")
    print("Class distribution:")
    print(out["current_classification"].value_counts().to_string())
    print("\nNext: fill the human_* columns, then run scripts/evaluate_labels.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
