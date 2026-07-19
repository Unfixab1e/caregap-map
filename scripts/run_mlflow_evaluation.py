"""Run the bounded MLflow 3 evaluation over a representative facility sample.

    python scripts/run_mlflow_evaluation.py [--data-dir data]
        [--experiment /Users/blubthefish@gmail.com/caregap-evaluation]
        [--per-class 6] [--max-sample 65]
        [--labels evals/private/icu_review.csv]
        [--codex-parquet data/processed/codex_pilot_snapshot.parquet]

OPTIONAL workflow: requires `pip install -e ".[mlflow]"` plus Databricks
authentication (MLFLOW_TRACKING_URI=databricks or databricks://<profile>).
The app, tests and deterministic pipeline never depend on it. Traces carry
identifiers, counts and scores only - never raw records or secrets.
See docs/MLFLOW_EVALUATION.md.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from caregap_map.mlflow_evaluation import DEFAULT_EXPERIMENT, MAX_SAMPLE, run_evaluation  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--experiment", default=DEFAULT_EXPERIMENT)
    parser.add_argument("--per-class", type=int, default=6)
    parser.add_argument("--max-sample", type=int, default=MAX_SAMPLE)
    parser.add_argument("--labels", default="evals/private/icu_review.csv")
    parser.add_argument(
        "--codex-parquet",
        default=None,
        help="STABLE Codex snapshot parquet (never the active batch-output directory).",
    )
    parser.add_argument("--out", default="reports/mlflow_eval_summary.json")
    parser.add_argument("--run-name", default=None)
    args = parser.parse_args()

    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=False
        ).stdout.strip()
    except OSError:
        commit = ""

    try:
        summary = run_evaluation(
            data_dir=args.data_dir,
            experiment=args.experiment,
            per_class=args.per_class,
            max_sample=args.max_sample,
            labels_path=args.labels,
            codex_parquet=args.codex_parquet,
            out_json=args.out,
            run_name=args.run_name,
            repo_commit=commit,
            timestamp=datetime.now(UTC).isoformat(timespec="seconds"),
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    printable = {k: v for k, v in summary.items() if not k.startswith("confusion")}
    print(json.dumps(printable, indent=2, ensure_ascii=False))
    print(f"\nSummary written to {args.out}")
    print(f"MLflow experiment: {summary['mlflow_experiment']} (run {summary['mlflow_run_id']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
