"""Profile the raw challenge datasets and write a machine-readable report.

Usage:
    python scripts/profile_data.py [--data-dir data] [--out reports/profile_report.json]

Exits non-zero with a clear message when a required file is missing or
malformed. Never modifies the raw files.
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

from caregap_map.cleaning import (  # noqa: E402
    NULL_LIKE,
    normalize_pincode,
    normalize_state,
    parse_coordinates,
)
from caregap_map.config import EVIDENCE_TEXT_FIELDS, DataPaths  # noqa: E402

EXPECTED_FILES = {
    "facilities": ("facilities.csv", 10_088, 51),
    "pin_directory": ("india_post_pincode_directory.csv", 165_627, 11),
    "nfhs": ("nfhs_5_district_health_indicators.csv", 706, 109),
}

KEY_EVIDENCE_FIELDS = EVIDENCE_TEXT_FIELDS + ["capacity", "numberDoctors", "source_urls"]


def null_like_share(series: pd.Series) -> dict:
    """Count truly-missing plus placeholder values ('null', '[]', ...)."""
    n = len(series)
    missing = int(series.isna().sum())
    placeholders = int(series.dropna().str.strip().str.lower().isin(NULL_LIKE).sum())
    populated = n - missing - placeholders
    return {
        "missing": missing,
        "null_like_placeholders": placeholders,
        "populated": populated,
        "populated_pct": round(100.0 * populated / n, 1) if n else 0.0,
    }


def profile_facilities(df: pd.DataFrame) -> dict:
    dup_ids = df["unique_id"].value_counts()
    dup_ids = dup_ids[dup_ids > 1]

    coord_status = pd.Series(
        [parse_coordinates(lat, lon)[2] for lat, lon in zip(df["latitude"], df["longitude"], strict=True)]
    )
    states = df["address_stateOrRegion"].map(normalize_state)
    pins = df["address_zipOrPostcode"].map(normalize_pincode)

    return {
        "rows": len(df),
        "columns": len(df.columns),
        "column_names": list(df.columns),
        "duplicate_unique_ids": {
            "n_ids_duplicated": int(len(dup_ids)),
            "n_rows_affected": int(dup_ids.sum()),
            "ids": dup_ids.index.tolist(),
        },
        "coordinate_validity": coord_status.value_counts().to_dict(),
        "geographic_coverage": {
            "state_field_resolvable": int(states.notna().sum()),
            "state_field_resolvable_pct": round(100.0 * states.notna().mean(), 1),
            "distinct_states_resolved": int(states.nunique()),
            "valid_pincode": int(pins.notna().sum()),
            "valid_pincode_pct": round(100.0 * pins.notna().mean(), 1),
        },
        "evidence_field_completion": {f: null_like_share(df[f]) for f in KEY_EVIDENCE_FIELDS},
    }


def profile_pin_directory(df: pd.DataFrame) -> dict:
    lat_numeric = pd.to_numeric(df["latitude"], errors="coerce")
    return {
        "rows": len(df),
        "columns": len(df.columns),
        "column_names": list(df.columns),
        "unique_pincodes": int(df["pincode"].nunique()),
        "rows_with_unparseable_or_missing_latitude": int(lat_numeric.isna().sum()),
        "distinct_states": int(df["statename"].nunique()),
    }


def profile_nfhs(df: pd.DataFrame) -> dict:
    non_numeric_cells = 0
    checked_cols = [c for c in df.columns if c not in ("district_name", "state_ut")]
    for col in checked_cols:
        values = df[col].dropna().str.strip()
        parsed = pd.to_numeric(values.str.replace(r"[(),*]", "", regex=True), errors="coerce")
        non_numeric_cells += int((parsed.isna() & (values != "")).sum())
    return {
        "rows": len(df),
        "columns": len(df.columns),
        "distinct_state_district_pairs": int(df.groupby(["state_ut", "district_name"]).ngroups),
        "indicator_columns": len(checked_cols),
        "cells_needing_numeric_cleaning": non_numeric_cells,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data", help="Directory containing raw/ (default: data)")
    parser.add_argument("--out", default="reports/profile_report.json", help="Where to write the JSON report")
    args = parser.parse_args()

    paths = DataPaths(data_dir=Path(args.data_dir))
    report: dict = {"data_dir": str(paths.data_dir), "datasets": {}}
    problems: list[str] = []

    profilers = {
        "facilities": (paths.facilities_csv, profile_facilities),
        "pin_directory": (paths.pin_directory_csv, profile_pin_directory),
        "nfhs": (paths.nfhs_csv, profile_nfhs),
    }

    for key, (path, profiler) in profilers.items():
        expected_name, expected_rows, expected_cols = EXPECTED_FILES[key]
        if not path.exists():
            problems.append(f"MISSING: expected {expected_name} at {path}")
            continue
        try:
            df = pd.read_csv(path, dtype=str)
        except Exception as exc:  # noqa: BLE001 - report and fail, never hide
            problems.append(f"MALFORMED: {path} could not be parsed as CSV ({exc})")
            continue
        stats = profiler(df)
        stats["dtypes_inferred"] = {c: str(t) for c, t in pd.read_csv(path, nrows=500).dtypes.items()}
        stats["expected_shape"] = [expected_rows, expected_cols]
        stats["shape_matches_expectation"] = (len(df) == expected_rows) and (len(df.columns) == expected_cols)
        report["datasets"][key] = stats
        print(
            f"[ok] {expected_name}: {len(df)} rows x {len(df.columns)} cols "
            f"(expected {expected_rows} x {expected_cols})"
        )

    report["problems"] = problems

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nReport written to {out_path}")

    if problems:
        print("\nPROBLEMS FOUND:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1

    fac = report["datasets"]["facilities"]
    print(
        f"  duplicate unique_ids: {fac['duplicate_unique_ids']['n_ids_duplicated']} "
        f"({fac['duplicate_unique_ids']['n_rows_affected']} rows)"
    )
    print(f"  coordinate validity: {fac['coordinate_validity']}")
    print(f"  geographic coverage: {fac['geographic_coverage']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
