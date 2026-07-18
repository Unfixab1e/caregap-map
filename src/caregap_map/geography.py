"""Geographic normalisation: PIN directory aggregation and facility geo-assignment.

The facility file's ``address_stateOrRegion`` column is unreliable (cities,
abbreviations, shifted columns). The India Post PIN directory is treated as
the authoritative source for state/district; the state text field is a
fallback. Every assignment records its provenance so the UI and validators
can distinguish confident joins from weak ones.
"""

from __future__ import annotations

import pandas as pd

from .cleaning import (
    normalize_null_like,
    normalize_pincode,
    normalize_state_verbose,
    parse_coordinates,
)


def aggregate_pin_directory(pin_df: pd.DataFrame) -> pd.DataFrame:
    """Collapse the PIN directory (one row per post office) to one row per PIN.

    Per PIN: modal state and district (post offices under one PIN almost
    always share both), the mean of the valid coordinates, and bookkeeping
    counts so weak aggregates can be identified.
    """
    df = pin_df.copy()
    df["pincode_clean"] = df["pincode"].map(normalize_pincode)
    df = df[df["pincode_clean"].notna()]

    parsed = [parse_coordinates(lat, lon) for lat, lon in zip(df["latitude"], df["longitude"], strict=True)]
    df["lat_parsed"] = [p[0] if p[2] == "ok" else None for p in parsed]
    df["lon_parsed"] = [p[1] if p[2] == "ok" else None for p in parsed]

    df["state_canon"] = df["statename"].map(lambda v: normalize_state_verbose(v)[0])
    df["district_clean"] = df["district"].map(lambda v: (normalize_null_like(v) or "").title() or None)

    def _mode(series: pd.Series) -> str | None:
        values = series.dropna()
        if values.empty:
            return None
        return values.mode().iloc[0]

    agg = df.groupby("pincode_clean").agg(
        pin_state=("state_canon", _mode),
        pin_district=("district_clean", _mode),
        pin_lat=("lat_parsed", "mean"),
        pin_lon=("lon_parsed", "mean"),
        n_offices=("pincode_clean", "size"),
        n_valid_coords=("lat_parsed", "count"),
        n_states_seen=("state_canon", "nunique"),
    )
    return agg.reset_index()


def assign_geography(fac_df: pd.DataFrame, pin_agg: pd.DataFrame) -> pd.DataFrame:
    """Attach canonical state/district to facilities with recorded provenance.

    Adds columns:

    - ``pincode_clean``: normalised 6-digit PIN (or None)
    - ``state_from_pin`` / ``district_from_pin``: from the PIN directory join
    - ``state_from_field`` / ``state_field_method``: from the messy state column
    - ``state_final``: PIN join wins, state field is the fallback
    - ``geo_source``: ``pin_directory`` | ``state_field`` | ``none``
    - ``geo_conflict``: True when both sources resolve and disagree
    """
    df = fac_df.copy()
    df["pincode_clean"] = df["address_zipOrPostcode"].map(normalize_pincode)

    lookup = pin_agg[["pincode_clean", "pin_state", "pin_district"]].drop_duplicates("pincode_clean")
    original_index = df.index
    df = df.merge(lookup, on="pincode_clean", how="left")
    df.index = original_index
    df["state_from_pin"] = df.pop("pin_state")
    df["district_from_pin"] = df.pop("pin_district")

    verbose = df["address_stateOrRegion"].map(normalize_state_verbose)
    df["state_from_field"] = [v[0] for v in verbose]
    df["state_field_method"] = [v[1] for v in verbose]

    df["state_final"] = df["state_from_pin"].where(df["state_from_pin"].notna(), df["state_from_field"])
    df["geo_source"] = "none"
    df.loc[df["state_from_field"].notna(), "geo_source"] = "state_field"
    df.loc[df["state_from_pin"].notna(), "geo_source"] = "pin_directory"

    both = df["state_from_pin"].notna() & df["state_from_field"].notna()
    df["geo_conflict"] = both & (df["state_from_pin"] != df["state_from_field"])
    return df
