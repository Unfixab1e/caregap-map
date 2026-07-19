"""Find safe demo candidates for the 90-second flow.

    python scripts/find_demo_facilities.py [--data-dir data]

Prints candidates per category (Trusted general ICU, Needs Review,
subtype-only records, data-desert / planning-gap / single-trusted
districts, persistence test data) and writes the full list to
reports/demo_facilities.md (git-ignored: real names + IDs). Read-only -
classifications are never changed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from caregap_map.config import DataPaths  # noqa: E402
from caregap_map.demo_candidates import find_demo_candidates, render_markdown  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--per-category", type=int, default=3)
    parser.add_argument("--out", default="reports/demo_facilities.md")
    args = parser.parse_args()

    paths = DataPaths(data_dir=Path(args.data_dir))
    if not paths.facilities_scored_parquet.exists():
        print("ERROR: run scripts/build_processed_data.py first.", file=sys.stderr)
        return 1
    scored = pd.read_parquet(paths.facilities_scored_parquet)
    region_district = pd.read_parquet(paths.region_district_parquet)

    candidates = find_demo_candidates(scored, region_district, per_category=args.per_category)
    markdown = render_markdown(candidates)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(markdown, encoding="utf-8")

    for category, rows in candidates.items():
        print(f"{category}: {len(rows)} candidate(s)")
    print(f"\nFull list (git-ignored): {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
