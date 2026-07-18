"""Reusable cleaning primitives for the raw challenge datasets.

Design rules:
- Never destroy information: original values are kept alongside cleaned
  ones (the pipeline stores ``*_raw`` columns / match provenance).
- Never silently drop rows: invalid values become flags, not deletions.
- All normalisation is deterministic and documented.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pandas as pd

# Literal strings that mean "no value" in the raw data.
NULL_LIKE = {"", "null", "none", "na", "n/a", "nan", "-", "--", "[]", "{}", "unknown", '""', "''"}

# India bounding box used to sanity-check coordinates (generous, includes islands).
INDIA_LAT_RANGE = (6.0, 38.0)
INDIA_LON_RANGE = (68.0, 98.0)


def normalize_null_like(value: Any) -> str | None:
    """Return a stripped string, or ``None`` for null-like placeholders."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if text.lower() in NULL_LIKE:
        return None
    return text


def parse_list_field(value: Any) -> list[str]:
    """Parse a raw JSON-array-ish field into a list of non-empty strings.

    Raw fields like ``capability`` and ``equipment`` hold JSON arrays as
    strings. Malformed values are kept as a single-item list rather than
    discarded, so no original text is lost.
    """
    text = normalize_null_like(value)
    if text is None:
        return []
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return [text]
    if isinstance(parsed, list):
        items = []
        for item in parsed:
            cleaned = normalize_null_like(item)
            if cleaned is not None:
                items.append(cleaned)
        return items
    cleaned = normalize_null_like(parsed)
    return [cleaned] if cleaned is not None else []


def parse_int_safe(value: Any) -> int | None:
    """Extract the first integer from a messy value, else ``None``."""
    text = normalize_null_like(value)
    if text is None:
        return None
    match = re.search(r"-?\d+", text.replace(",", ""))
    return int(match.group()) if match else None


def parse_float_safe(value: Any) -> float | None:
    """Extract the first float from a messy value, else ``None``.

    Handles NFHS-style values such as ``(29.5)``, ``*``, ``1,234`` and text.
    """
    text = normalize_null_like(value)
    if text is None or text == "*":
        return None
    text = text.replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(match.group()) if match else None


def parse_coordinates(lat: Any, lon: Any) -> tuple[float | None, float | None, str]:
    """Parse a latitude/longitude pair and classify its validity.

    Returns ``(lat, lon, status)`` with status one of:
    ``ok`` | ``missing`` | ``unparseable`` | ``out_of_range``.
    Out-of-range and (0, 0) coordinates keep their parsed values so they
    remain inspectable, but are flagged instead of silently dropped.
    """
    lat_f = parse_float_safe(lat)
    lon_f = parse_float_safe(lon)
    if normalize_null_like(lat) is None and normalize_null_like(lon) is None:
        return None, None, "missing"
    if lat_f is None or lon_f is None:
        return lat_f, lon_f, "unparseable"
    if lat_f == 0.0 and lon_f == 0.0:
        return lat_f, lon_f, "out_of_range"
    if not (INDIA_LAT_RANGE[0] <= lat_f <= INDIA_LAT_RANGE[1]) or not (
        INDIA_LON_RANGE[0] <= lon_f <= INDIA_LON_RANGE[1]
    ):
        return lat_f, lon_f, "out_of_range"
    return lat_f, lon_f, "ok"


def normalize_pincode(value: Any) -> str | None:
    """Extract a plausible 6-digit Indian PIN code, else ``None``."""
    text = normalize_null_like(value)
    if text is None:
        return None
    match = re.search(r"(?<!\d)([1-9]\d{5})(?!\d)", text.replace(" ", ""))
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# State normalisation
# ---------------------------------------------------------------------------

CANONICAL_STATES = [
    "Andaman and Nicobar Islands",
    "Andhra Pradesh",
    "Arunachal Pradesh",
    "Assam",
    "Bihar",
    "Chandigarh",
    "Chhattisgarh",
    "Dadra and Nagar Haveli and Daman and Diu",
    "Delhi",
    "Goa",
    "Gujarat",
    "Haryana",
    "Himachal Pradesh",
    "Jammu and Kashmir",
    "Jharkhand",
    "Karnataka",
    "Kerala",
    "Ladakh",
    "Lakshadweep",
    "Madhya Pradesh",
    "Maharashtra",
    "Manipur",
    "Meghalaya",
    "Mizoram",
    "Nagaland",
    "Odisha",
    "Puducherry",
    "Punjab",
    "Rajasthan",
    "Sikkim",
    "Tamil Nadu",
    "Telangana",
    "Tripura",
    "Uttar Pradesh",
    "Uttarakhand",
    "West Bengal",
]

# Aliases keyed by the *normalised* form produced by ``_state_key``.
# Only unambiguous spellings/abbreviations observed in the data are mapped.
STATE_ALIASES = {
    "orissa": "Odisha",
    "uttaranchal": "Uttarakhand",
    "uttranchal": "Uttarakhand",
    "uk": "Uttarakhand",
    "up": "Uttar Pradesh",
    "u p": "Uttar Pradesh",
    "uttarpradesh": "Uttar Pradesh",
    "uttar prades h": "Uttar Pradesh",
    "mp": "Madhya Pradesh",
    "m p": "Madhya Pradesh",
    "madhyapradesh": "Madhya Pradesh",
    "madhya": "Madhya Pradesh",
    "mh": "Maharashtra",
    "ms": "Maharashtra",
    "maharastra": "Maharashtra",
    "gj": "Gujarat",
    "br": "Bihar",
    "cg": "Chhattisgarh",
    "chattisgarh": "Chhattisgarh",
    "ts": "Telangana",
    "telengana": "Telangana",
    "dl": "Delhi",
    "new delhi": "Delhi",
    "nct": "Delhi",
    "nct delhi": "Delhi",
    "nct of delhi": "Delhi",
    "delhi ncr": "Delhi",
    "ncr delhi": "Delhi",
    "east delhi": "Delhi",
    "west delhi": "Delhi",
    "south delhi": "Delhi",
    "north west delhi": "Delhi",
    "south east delhi area": "Delhi",
    "tamilnadu": "Tamil Nadu",
    "pondicherry": "Puducherry",
    "u t of puducherry": "Puducherry",
    "kashmir": "Jammu and Kashmir",
    "srinagar kashmir": "Jammu and Kashmir",
    "jammu j and k": "Jammu and Kashmir",
    "j and k": "Jammu and Kashmir",
    "the dadra and nagar haveli and daman and diu": "Dadra and Nagar Haveli and Daman and Diu",
    "dadra and nagar haveli": "Dadra and Nagar Haveli and Daman and Diu",
    "daman and diu": "Dadra and Nagar Haveli and Daman and Diu",
}


def _state_key(value: str) -> str:
    """Normalise a state string for lookup: lowercase, '&'->'and', strip punctuation."""
    key = value.lower().replace("&", " and ")
    key = re.sub(r"[^\w\s]", " ", key)
    key = re.sub(r"\s+", " ", key).strip()
    return key


_CANONICAL_BY_KEY = {_state_key(s): s for s in CANONICAL_STATES}


def normalize_state_verbose(value: Any) -> tuple[str | None, str]:
    """Map a messy state/region value to a canonical state name.

    Returns ``(canonical_state | None, method)`` with method one of:

    - ``exact``: normalised text equals a canonical state name
    - ``alias``: known abbreviation or misspelling
    - ``suffix``: text *ends with* a canonical state name
      (e.g. ``"Ghaziabad, Uttar Pradesh"``) - lower confidence
    - ``none``: no defensible match (city names alone are NOT guessed)
    """
    text = normalize_null_like(value)
    if text is None:
        return None, "none"
    key = _state_key(text)
    if key in _CANONICAL_BY_KEY:
        return _CANONICAL_BY_KEY[key], "exact"
    if key in STATE_ALIASES:
        return STATE_ALIASES[key], "alias"
    for canon_key, canon in _CANONICAL_BY_KEY.items():
        if key.endswith(" " + canon_key):
            return canon, "suffix"
    return None, "none"


def normalize_state(value: Any) -> str | None:
    """Convenience wrapper for :func:`normalize_state_verbose`."""
    return normalize_state_verbose(value)[0]


# ---------------------------------------------------------------------------
# Dataframe-level helpers
# ---------------------------------------------------------------------------


def dedupe_facilities(df: pd.DataFrame, id_column: str = "unique_id") -> tuple[pd.DataFrame, int]:
    """Conservatively deduplicate facilities on their unique id.

    Keeps the most complete row (highest count of populated fields) per id.
    Only exact id duplicates are collapsed; same-name facilities keep
    separate rows and are merely *flagged* downstream.
    Returns the deduplicated frame and the number of rows dropped.
    """
    before = len(df)
    completeness = df.notna().sum(axis=1)
    order = completeness.sort_values(ascending=False, kind="stable").index
    deduped = df.loc[order].drop_duplicates(subset=[id_column], keep="first")
    deduped = deduped.sort_index()
    return deduped, before - len(deduped)


def clean_nfhs(df: pd.DataFrame) -> pd.DataFrame:
    """Clean the NFHS-5 district indicator table.

    Keeps district/state as normalised strings and converts every indicator
    column with :func:`parse_float_safe` (handles ``(29.5)``, ``*``, text).
    Original string values are preserved in the returned ``*_raw`` columns
    only for the identifier fields; indicator columns become floats.
    """
    columns: dict[str, pd.Series] = {
        "district_name": df["district_name"].map(normalize_null_like),
        "state_raw": df["state_ut"],
        "state": df["state_ut"].map(normalize_state),
    }
    for col in df.columns:
        if col in ("district_name", "state_ut"):
            continue
        columns[col] = df[col].map(parse_float_safe)
    return pd.DataFrame(columns, index=df.index)
