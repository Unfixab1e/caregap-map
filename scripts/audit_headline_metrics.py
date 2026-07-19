"""Reproducible audit of the app's headline metrics.

    python scripts/audit_headline_metrics.py [--data-dir data]

Reads the processed facility data and writes

    reports/headline_metric_audit.json
    reports/headline_metric_audit.md

Both outputs may contain real record identifiers, so they live under
reports/ (git-ignored). The audit logic itself is in
:mod:`caregap_map.audit` and is covered by synthetic tests - neither the
code nor the tests require the real challenge data.
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

from caregap_map.audit import build_audit_report, render_markdown  # noqa: E402
from caregap_map.config import DataPaths, load_scoring_config  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--out-json", default="reports/headline_metric_audit.json")
    parser.add_argument("--out-md", default="reports/headline_metric_audit.md")
    args = parser.parse_args()

    paths = DataPaths(data_dir=Path(args.data_dir))
    if not paths.facilities_scored_parquet.exists():
        print(
            f"ERROR: {paths.facilities_scored_parquet} not found - "
            "run scripts/build_processed_data.py first.",
            file=sys.stderr,
        )
        return 1

    scored = pd.read_parquet(paths.facilities_scored_parquet)
    report = build_audit_report(scored, load_scoring_config())

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    Path(args.out_md).write_text(render_markdown(report), encoding="utf-8")

    jud = report["judgeability"]
    gap = report["gap_records"]
    print(f"Records: {report['records_total']}")
    print(f"Judgeable: {jud['records_judgeable']} ({jud['pct_judgeable_exact']}%)")
    print(
        f"Gap records: {gap['gap_records_total']} - hospital-like "
        f"{gap['hospital_like']['count']}, clearly non-hospital "
        f"{gap['clearly_non_hospital']['count']}, clinic/health-centre "
        f"{gap['ambiguous_clinic_or_health_center']}, unknown {gap['uncategorizable']}"
    )
    print(f"Wrote {out_json} and {args.out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
