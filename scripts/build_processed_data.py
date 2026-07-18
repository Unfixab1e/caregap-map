"""Build processed Parquet datasets from the raw challenge CSVs.

Usage:
    python scripts/build_processed_data.py [--data-dir data]

Outputs (under <data-dir>/processed/):
    facilities_clean.parquet      cleaned facilities with geo provenance
    facilities_scored.parquet     + evidence/completeness scores and classes
    pin_directory_agg.parquet     one geographic record per PIN code
    nfhs_clean.parquet            NFHS-5 indicators with robust numeric parsing
    region_summary_state.parquet  regional aggregates (state level)
    region_summary_district.parquet
    cleaning_summary.json         what was changed, dropped and flagged

The raw CSVs are never modified. Rows are flagged, not silently dropped
(the only removals are exact unique_id duplicates, and they are counted).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from caregap_map.aggregation import aggregate_regions  # noqa: E402
from caregap_map.cleaning import (  # noqa: E402
    clean_nfhs,
    dedupe_facilities,
    parse_coordinates,
    parse_int_safe,
)
from caregap_map.config import DataPaths, load_scoring_config  # noqa: E402
from caregap_map.data_access import LocalDataSource  # noqa: E402
from caregap_map.geography import aggregate_pin_directory, assign_geography  # noqa: E402
from caregap_map.scoring import score_dataframe  # noqa: E402

# Raw columns carried into the processed facilities table. The raw CSV stays
# the source of truth; unused web/social-media metrics are not carried over.
FACILITY_KEEP_COLUMNS = [
    "unique_id",
    "name",
    "organization_type",
    "address_line1",
    "address_city",
    "address_stateOrRegion",
    "address_zipOrPostcode",
    "description",
    "area",
    "numberDoctors",
    "capacity",
    "specialties",
    "procedure",
    "equipment",
    "capability",
    "source",
    "source_urls",
    "latitude",
    "longitude",
    "cluster_id",
]


def build(data_dir: Path) -> dict:
    paths = DataPaths(data_dir=data_dir)
    source = LocalDataSource(paths)
    config = load_scoring_config()
    summary: dict = {"started_at": time.strftime("%Y-%m-%dT%H:%M:%S")}

    # --- PIN directory -> one record per PIN --------------------------------
    print("Aggregating PIN directory ...")
    pin_raw = source.load_pin_directory_raw()
    pin_agg = aggregate_pin_directory(pin_raw)
    summary["pin_directory"] = {
        "rows_in": len(pin_raw),
        "pins_out": len(pin_agg),
        "pins_with_valid_coords": int((pin_agg["n_valid_coords"] > 0).sum()),
        "pins_spanning_multiple_states": int((pin_agg["n_states_seen"] > 1).sum()),
    }

    # --- Facilities ---------------------------------------------------------
    print("Cleaning facilities ...")
    fac_raw = source.load_facilities_raw()
    fac, n_dupes_dropped = dedupe_facilities(fac_raw)
    fac = fac[FACILITY_KEEP_COLUMNS].copy()

    coords = [parse_coordinates(lat, lon) for lat, lon in zip(fac["latitude"], fac["longitude"])]
    fac["lat_parsed"] = [c[0] for c in coords]
    fac["lon_parsed"] = [c[1] for c in coords]
    fac["coord_status"] = [c[2] for c in coords]
    fac["capacity_int"] = fac["capacity"].map(parse_int_safe)
    fac["number_doctors_int"] = fac["numberDoctors"].map(parse_int_safe)

    fac = assign_geography(fac, pin_agg)
    fac["district_final"] = fac["district_from_pin"]

    summary["facilities"] = {
        "rows_in": len(fac_raw),
        "exact_duplicate_rows_dropped": n_dupes_dropped,
        "rows_out": len(fac),
        "coord_status_counts": fac["coord_status"].value_counts().to_dict(),
        "geo_source_counts": fac["geo_source"].value_counts().to_dict(),
        "geo_conflicts": int(fac["geo_conflict"].sum()),
        "rows_with_state": int(fac["state_final"].notna().sum()),
        "rows_with_district": int(fac["district_final"].notna().sum()),
    }

    # --- NFHS (secondary; cleaned for later use, not joined yet) ------------
    print("Cleaning NFHS indicators ...")
    nfhs_clean = clean_nfhs(source.load_nfhs_raw())
    summary["nfhs"] = {
        "rows": len(nfhs_clean),
        "states_resolved": int(nfhs_clean["state"].notna().sum()),
    }

    # --- Scoring ------------------------------------------------------------
    print("Scoring facilities (deterministic evidence extraction) ...")
    scores = score_dataframe(fac, config)
    scored = pd.concat([fac, scores], axis=1)
    summary["scoring"] = {
        "classification_counts": scored["classification"].value_counts().to_dict(),
        "explicit_icu_claims": int(scored["explicit_icu_claim"].sum()),
        "facilities_with_contradictions": int((scored["n_contradiction_flags"] > 0).sum()),
        "mean_evidence_score": round(float(scored["capability_evidence_score"].mean()), 1),
        "mean_completeness_score": round(float(scored["data_completeness_score"].mean()), 1),
    }

    # --- Regional aggregation ------------------------------------------------
    print("Aggregating regions ...")
    region_state = aggregate_regions(scored, "state", config)
    region_district = aggregate_regions(scored, "district", config)
    summary["regions"] = {
        "states": len(region_state),
        "districts": len(region_district),
        "state_status_counts": region_state["region_status"].value_counts().to_dict(),
    }

    # --- Write outputs --------------------------------------------------------
    paths.processed_dir.mkdir(parents=True, exist_ok=True)
    fac.to_parquet(paths.facilities_clean_parquet, index=False)
    scored.to_parquet(paths.facilities_scored_parquet, index=False)
    pin_agg.to_parquet(paths.pin_agg_parquet, index=False)
    nfhs_clean.to_parquet(paths.nfhs_clean_parquet, index=False)
    region_state.to_parquet(paths.region_state_parquet, index=False)
    region_district.to_parquet(paths.region_district_parquet, index=False)
    paths.cleaning_summary_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"\nProcessed outputs written to {paths.processed_dir}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data", help="Directory containing raw/ (default: data)")
    args = parser.parse_args()
    try:
        summary = build(Path(args.data_dir))
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
