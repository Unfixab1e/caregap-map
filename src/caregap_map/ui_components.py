"""UI data-preparation helpers: priority facilities, distribution, examples.

Presentation-side selection and formatting only (D24): everything is
derived from existing classifications, scores and validator flags - no
new risk score, ranking model or classification logic.
"""

from __future__ import annotations

import json
import math
import re

import pandas as pd

from .config import (
    CLASS_INSUFFICIENT,
    CLASS_LIKELY_GAP,
    CLASS_NEEDS_REVIEW,
    CLASS_TRUSTED,
    REGION_DATA_DESERT,
    REGION_NEEDS_REVIEW,
    REGION_PLANNING_GAP,
    facility_display_label,
)

PRIORITY_LIMIT = 5

# ---------------------------------------------------------------------------
# Facility context for general-ICU planning (D27)
# ---------------------------------------------------------------------------
# UI-focused, deterministic and conservative: describes what kind of
# organization a record APPEARS to be, purely as review-priority context for
# general-adult-ICU planning. It is separate from - and never modifies - the
# ICU evidence classification, and it makes no clinical claim (a specialty
# hospital may well run an ICU; an outpatient record is not "bad data").

CONTEXT_GENERAL = "Likely general-hospital context"
CONTEXT_SPECIALTY = "Specialty or limited-scope hospital context"
CONTEXT_OUTPATIENT = "Outpatient / diagnostic context"
CONTEXT_UNKNOWN = "Unknown context"

FACILITY_CONTEXTS = [CONTEXT_GENERAL, CONTEXT_SPECIALTY, CONTEXT_OUTPATIENT, CONTEXT_UNKNOWN]

# Review-priority rank within otherwise comparable records (lower = earlier).
CONTEXT_PRIORITY_RANK = {
    CONTEXT_GENERAL: 0,
    CONTEXT_SPECIALTY: 1,
    CONTEXT_UNKNOWN: 2,
    CONTEXT_OUTPATIENT: 3,
}

CONTEXT_CAPTION = (
    "Facility context affects review priority only; it does not change the "
    "ICU evidence score or classification."
)

_HOSPITAL_WORD = re.compile(
    r"\b(hospitals?|nursing home|medical college|institute of medical|sanatorium)\b", re.I
)
_GENERAL_MARKERS = re.compile(
    r"\b(multi[\s-]?special(i?ty|ities)|super[\s-]?special(i?ty|ities)|general hospital|"
    r"district hospital|civil hospital|medical college|government hospital)\b",
    re.I,
)
_SPECIALTY_MARKERS = re.compile(
    r"\b(eye|ent|opthal|ophthal|dental|tooth|skin|hair|cosmet|kidney|stone|urolog|ortho|"
    r"maternity|children|child|p(?:ae|e)diatric|cancer|onco|cardiac|heart|mental|"
    r"psychiatr|ayurved|homoeo|homeo|fertility|ivf|gyn(?:ae|e)c?|obstetric|piles|"
    r"pulmo|chest|liver|gastro|neuro|spine|joint|knee)\b",
    re.I,
)
_OUTPATIENT_MARKERS = re.compile(
    r"\b(dental|dentist|dentistry|tooth|pharmacy|pharmacies|chemist|medical store|"
    r"drug\s?store|path\s?labs?|pathology|patholab|diagnostics?|laborator(y|ies)|labs?|"
    r"scans?|scanning|imaging|x-?rays?|radiology|mri|sonography|ultrasound|"
    r"physiotherapy|physio)\b",
    re.I,
)
_CLINIC_WORD = re.compile(r"\b(clinics?|polyclinics?|dispensar(y|ies)|care cent(er|re)s?)\b", re.I)
_DOCTOR_PREFIX_CTX = re.compile(r"^\s*(dr\.?|doctor)\s+", re.I)


def facility_context(name: object, organization_type: object = None) -> str:
    """Conservative name-based context for general-ICU review priority.

    Never used for scoring, classification or aggregation; ambiguous names
    stay "Unknown context" rather than being guessed.
    """
    text = " ".join(
        str(v).strip() for v in (name, organization_type) if isinstance(v, str) and v.strip()
    )
    text = re.sub(r"\bfacility\b", " ", text, flags=re.I).strip()
    if not text:
        return CONTEXT_UNKNOWN
    has_hospital_word = bool(_HOSPITAL_WORD.search(text))
    if not has_hospital_word:
        if _OUTPATIENT_MARKERS.search(text) or _CLINIC_WORD.search(text):
            return CONTEXT_OUTPATIENT
        if _DOCTOR_PREFIX_CTX.search(text):
            return CONTEXT_OUTPATIENT
        return CONTEXT_UNKNOWN
    if _SPECIALTY_MARKERS.search(text):
        return CONTEXT_SPECIALTY
    if _GENERAL_MARKERS.search(text):
        return CONTEXT_GENERAL
    # A plain, unqualified "hospital"/"nursing home" name: likely general.
    return CONTEXT_GENERAL


def _column(df: pd.DataFrame, name: str) -> list:
    """Column values, or Nones when the column is absent (synthetic frames)."""
    return df[name].tolist() if name in df.columns else [None] * len(df)


def _contexts(df: pd.DataFrame) -> list[str]:
    return [
        facility_context(name, org)
        for name, org in zip(_column(df, "name"), _column(df, "organization_type"), strict=True)
    ]


def facility_mix_counts(subset: pd.DataFrame) -> dict[str, int]:
    """Counts per facility context for the current selection (display only)."""
    counts = dict.fromkeys(FACILITY_CONTEXTS, 0)
    for context in _contexts(subset):
        counts[context] += 1
    return counts


def facility_mix_sentence(counts: dict[str, int], total: int) -> str:
    """'Of 10 supplied records, 4 appear to be general-hospital contexts, …'"""
    return (
        f"Of {total} supplied records, {counts.get(CONTEXT_GENERAL, 0)} appear to be "
        f"general-hospital contexts, {counts.get(CONTEXT_SPECIALTY, 0)} specialty contexts, "
        f"{counts.get(CONTEXT_OUTPATIENT, 0)} outpatient/diagnostic contexts and "
        f"{counts.get(CONTEXT_UNKNOWN, 0)} unknown."
    )


MIX_GAP_WARNING = (
    "Records from clinics, laboratories or other limited-scope providers provide weak "
    "evidence about general adult ICU capability. Verify the facility mix before "
    "interpreting this regional result."
)


def mix_warning_applies(counts: dict[str, int]) -> bool:
    """Show the planning-gap facility-mix warning only when relevant."""
    outpatient = counts.get(CONTEXT_OUTPATIENT, 0)
    general = counts.get(CONTEXT_GENERAL, 0)
    return outpatient > 0 and (general == 0 or outpatient >= general)


def humanize_flag(name: str) -> str:
    """`directory_or_partner_content_detected` -> `Directory or partner content detected`."""
    return name.replace("_", " ").strip().capitalize()


def primary_flag(row: pd.Series | dict) -> str | None:
    """The first validator flag name, humanized - or None."""
    raw = row.get("validation_flags_json")
    if not isinstance(raw, str) or not raw:
        return None
    flags = json.loads(raw)
    if not flags:
        return None
    return humanize_flag(flags[0].get("name", ""))


NEAR_TRUSTED_REASON = "Near Trusted — review corroboration"


def priority_reasonings() -> dict[str, str]:
    """Priority tiers and their transparent, fixed explanations."""
    return {
        "near_trusted": NEAR_TRUSTED_REASON,
        "review": "Unresolved ICU claim — human review required",
        "trusted_flagged": "Trusted evidence carrying validator flags — spot-check first",
        "trusted": "Trusted evidence — verify operational details",
        "gap": "Well-populated record without ICU evidence",
    }


def review_priority_rank(row: pd.Series | dict, trust_threshold: int = 45) -> int:
    """Transparent ordering WITHIN Needs Human Review (D28, display only):

    0 — near Trusted: high evidence, explicit claim, no blocking flags
        (one corroboration category short);
    1 — high evidence with suspicious-content flags;
    2 — ambiguous evidence scores;
    3 — contradictory claims.

    A review-priority label only - it never changes classification.
    """
    if int(row.get("n_contradiction_flags") or 0) > 0:
        return 3
    flags_raw = row.get("validation_flags_json")
    flags = json.loads(flags_raw) if isinstance(flags_raw, str) and flags_raw else []
    has_suspicious = any(f.get("severity") == "suspicious" for f in flags)
    high = int(row.get("capability_evidence_score") or 0) >= trust_threshold
    if high and has_suspicious:
        return 1
    if high and bool(row.get("explicit_icu_claim")):
        return 0
    return 2


def select_priority_facilities(
    subset: pd.DataFrame, region_status: str, limit: int = PRIORITY_LIMIT
) -> pd.DataFrame:
    """Up to ``limit`` facilities a planner should look at first.

    Transparent tiering over EXISTING statuses and flags only (no opaque
    risk score): 1) Needs Human Review, 2) Trusted records with validator/
    data-quality flags, 3) other Trusted records, 4) no-ICU-evidence records
    only when the region is a potential planning gap. Within each tier,
    facility CONTEXT ranks first (general hospital > specialty hospital >
    unknown > outpatient/diagnostic, D27) so labs and dental clinics never
    crowd out hospital-like records - the context affects ordering only and
    never removes a record from the data. Returns a copy with
    ``priority_reason`` and ``facility_context`` columns.
    """
    reasons = priority_reasonings()
    work = subset.copy()
    work["facility_context"] = _contexts(work)
    work["_ctx_rank"] = work["facility_context"].map(CONTEXT_PRIORITY_RANK)
    parts: list[pd.DataFrame] = []

    review = work[work["classification"] == CLASS_NEEDS_REVIEW].copy()
    if len(review):
        review["_review_rank"] = [review_priority_rank(r) for _, r in review.iterrows()]
        review = review.sort_values(
            ["_review_rank", "_ctx_rank", "capability_evidence_score", "unique_id"],
            ascending=[True, True, False, True],
        )
        review["priority_reason"] = review["_review_rank"].map(
            lambda rank: reasons["near_trusted"] if rank == 0 else reasons["review"]
        )
        review = review.drop(columns=["_review_rank"])
    parts.append(review)

    trusted = work[work["classification"] == CLASS_TRUSTED]
    flagged = trusted[trusted["n_validation_flags"] > 0].sort_values(
        ["_ctx_rank", "n_validation_flags", "unique_id"], ascending=[True, False, True]
    )
    parts.append(flagged.assign(priority_reason=reasons["trusted_flagged"]))
    clean = trusted[trusted["n_validation_flags"] == 0].sort_values(
        ["_ctx_rank", "capability_evidence_score", "unique_id"], ascending=[True, False, True]
    )
    parts.append(clean.assign(priority_reason=reasons["trusted"]))

    if region_status == REGION_PLANNING_GAP:
        gaps = work[work["classification"] == CLASS_LIKELY_GAP].sort_values(
            ["_ctx_rank", "data_completeness_score", "unique_id"], ascending=[True, False, True]
        )
        parts.append(gaps.assign(priority_reason=reasons["gap"]))

    if not parts:
        return subset.head(0).assign(priority_reason="", facility_context="")
    merged = pd.concat(parts).drop_duplicates("unique_id").drop(columns=["_ctx_rank"])
    return merged.head(limit)


def hero_counts_html(summary: dict) -> str:
    """The hero-card count line as HTML (rendered with unsafe_allow_html).

    Markdown syntax is NOT interpreted inside raw-HTML blocks, so bolding
    must use <strong>, never ``**``. Only integer counts from the regional
    summary are interpolated - no user-supplied content enters this HTML.
    """

    def strong(key: str) -> str:
        return f"<strong>{int(summary.get(key, 0))}</strong>"

    return (
        f"{strong('facility_count')} supplied records &nbsp;·&nbsp; "
        f"🟢 {strong('trusted_icu_count')} trusted evidence &nbsp;·&nbsp; "
        f"🟡 {strong('needs_review_count')} need review &nbsp;·&nbsp; "
        f"🔴 {strong('likely_gap_count')} show no ICU evidence &nbsp;·&nbsp; "
        f"⚪ {strong('insufficient_data_count')} insufficient"
    )


def status_distribution(subset: pd.DataFrame) -> list[dict]:
    """Counts + percentages per evidence status, in stable display order."""
    n = len(subset)
    counts = subset["classification"].value_counts() if n else {}
    out = []
    for cls in (CLASS_TRUSTED, CLASS_NEEDS_REVIEW, CLASS_LIKELY_GAP, CLASS_INSUFFICIENT):
        count = int(counts.get(cls, 0)) if n else 0
        out.append(
            {
                "classification": cls,
                "label": facility_display_label(cls),
                "count": count,
                "pct": round(100.0 * count / n, 1) if n else 0.0,
            }
        )
    return out


# District facility-map markers: clearly visible at the default zoom.
# (MapLibre scatter markers do not support an outline stroke, so visibility
# comes from size, opacity and the validated status palette.)
MAP_MARKER = {"size": 14, "opacity": 0.9}
MAP_MIN_ZOOM = 6.0
MAP_MAX_ZOOM = 13.0


def map_view(lats: list[float], lons: list[float]) -> tuple[float, float, float]:
    """Deterministic bounds-aware map view: (center_lat, center_lon, zoom).

    Pure function of the coordinate extents - the coordinates themselves are
    never modified or jittered. Identical/near-identical points get the
    maximum zoom; wide spreads clamp to the minimum, so the view stays
    sensible for one facility, overlapping facilities and whole districts.
    """
    lat_min, lat_max = min(lats), max(lats)
    lon_min, lon_max = min(lons), max(lons)
    span = max(lat_max - lat_min, lon_max - lon_min, 0.01)
    zoom = math.log2(360.0 / span) - 1.5
    zoom = min(MAP_MAX_ZOOM, max(MAP_MIN_ZOOM, zoom))
    return ((lat_min + lat_max) / 2.0, (lon_min + lon_max) / 2.0, round(zoom, 2))


def district_centroids(scored: pd.DataFrame, region_district: pd.DataFrame) -> pd.DataFrame:
    """One row per district for the national evidence landscape (D25).

    Built ENTIRELY from existing processed data: the centroid is the median
    of the district's validly-located facility coordinates (median resists
    outlier points), joined with the existing district regional summary for
    status and counts. No external boundary geometry, no new score.
    Districts without any usable coordinates - and unassigned rows - are
    excluded here and must be counted in a caption by the caller.
    """
    located = scored[scored["coord_status"] == "ok"]
    located = located[located["state_final"].notna() & located["district_final"].notna()]
    if located.empty:
        return pd.DataFrame(
            columns=["state", "district", "lat", "lon", "region_status", "facility_count"]
        )
    centroids = (
        located.groupby(["state_final", "district_final"])[["lat_parsed", "lon_parsed"]]
        .median()
        .reset_index()
        .rename(
            columns={
                "state_final": "state",
                "district_final": "district",
                "lat_parsed": "lat",
                "lon_parsed": "lon",
            }
        )
    )
    regions = region_district[
        (region_district["state"] != "(unassigned)")
        & (region_district["district"] != "(unassigned)")
    ][
        [
            "state",
            "district",
            "region_status",
            "facility_count",
            "trusted_icu_count",
            "needs_review_count",
            "likely_gap_count",
            "insufficient_data_count",
            "pct_sufficient_data",
        ]
    ]
    return centroids.merge(regions, on=["state", "district"], how="inner").sort_values(
        ["state", "district"], ignore_index=True
    )


def _best_planning_gap_example(
    candidates: pd.DataFrame, scored: pd.DataFrame
) -> tuple[str, str] | None:
    """Prefer a demo-friendly planning-gap district, deterministically.

    Ranks candidate districts by: number of likely general-hospital-context
    records (desc), share of outpatient/diagnostic records (asc), number of
    validly-located records (desc), record count (desc), then name. Uses
    display-only context labels - regional statuses are taken as stored.
    """
    ranked: list[tuple] = []
    for _, row in candidates.iterrows():
        records = scored[
            (scored["state_final"] == row["state"])
            & (scored["district_final"] == row["district"])
        ]
        if records.empty:
            continue
        contexts = _contexts(records)
        n_general = sum(c == CONTEXT_GENERAL for c in contexts)
        outpatient_share = sum(c == CONTEXT_OUTPATIENT for c in contexts) / len(contexts)
        n_located = int((records["coord_status"] == "ok").sum())
        ranked.append(
            (
                -n_general,
                round(outpatient_share, 4),
                -n_located,
                -int(row["facility_count"]),
                str(row["state"]),
                str(row["district"]),
            )
        )
    if not ranked:
        return None
    best = min(ranked)
    return (best[4], best[5])


def example_regions(
    region_district: pd.DataFrame, scored: pd.DataFrame | None = None
) -> dict[str, tuple[str, str]]:
    """Up to three deterministic demo selections from the CURRENT data.

    Per showcased regional status the default pick is the district with the
    most records (ties broken by name). For the planning-gap example, when
    ``scored`` is provided, districts with several general-hospital-context
    records, fewer outpatient/diagnostic records and multiple usable
    coordinates are preferred (D27) - falling back to the default pick.
    Nothing is hard-coded. Returns {label: (state, district)}.
    """
    examples: dict[str, tuple[str, str]] = {}
    wanted = [
        ("Data desert", REGION_DATA_DESERT),
        ("Potential planning gap", REGION_PLANNING_GAP),
        ("Needs facility verification", REGION_NEEDS_REVIEW),
    ]
    for label, status in wanted:
        rows = region_district[
            (region_district["region_status"] == status)
            & (region_district["facility_count"] > 0)
            & (region_district["state"] != "(unassigned)")
            & (region_district["district"] != "(unassigned)")
        ].sort_values(["facility_count", "state", "district"], ascending=[False, True, True])
        if not len(rows):
            continue
        pick: tuple[str, str] | None = None
        if status == REGION_PLANNING_GAP and scored is not None:
            demo_worthy = rows[rows["facility_count"] >= 5]
            pick = _best_planning_gap_example(
                demo_worthy if len(demo_worthy) else rows, scored
            )
        if pick is None:
            first = rows.iloc[0]
            pick = (str(first["state"]), str(first["district"]))
        examples[label] = pick
    return examples
