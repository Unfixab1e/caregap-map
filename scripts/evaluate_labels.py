"""Score the human-labelled evaluation file against both extractors.

    python scripts/evaluate_labels.py [--labels evals/private/icu_review.csv]

Writes reports/label_eval_report.json and prints a summary. Runs gracefully
while labels are still incomplete (pending rows are reported, not scored).
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

from caregap_map.evaluation import evaluate_labels  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", default="evals/private/icu_review.csv")
    parser.add_argument("--out", default="reports/label_eval_report.json")
    args = parser.parse_args()

    labels_path = Path(args.labels)
    if not labels_path.exists():
        print(
            f"ERROR: {labels_path} not found - run scripts/build_eval_sample.py first.",
            file=sys.stderr,
        )
        return 1

    df = pd.read_csv(labels_path, dtype=str)
    report = evaluate_labels(df)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"\nReport written to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
