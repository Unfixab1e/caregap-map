"""UI data-preparation helpers: priority facilities, distribution, examples.

Presentation-side selection and formatting only (D24): everything is
derived from existing classifications, scores and validator flags - no
new risk score, ranking model or classification logic.
"""

from __future__ import annotations

import json

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


def priority_reasonings() -> dict[str, str]:
    """Priority tiers and their transparent, fixed explanations."""
    return {
        "review": "Unresolved ICU claim — human review required",
        "trusted_flagged": "Trusted evidence carrying validator flags — spot-check first",
        "trusted": "Trusted evidence — verify operational details",
        "gap": "Well-populated record without ICU evidence",
    }


def select_priority_facilities(
    subset: pd.DataFrame, region_status: str, limit: int = PRIORITY_LIMIT
) -> pd.DataFrame:
    """Up to ``limit`` facilities a planner should look at first.

    Transparent tiering over EXISTING statuses and flags only (no opaque
    risk score): 1) Needs Human Review (strongest claims first), 2) Trusted
    records with validator/data-quality flags, 3) other Trusted records,
    4) no-ICU-evidence records only when the region is a potential planning
    gap (they then carry the regional conclusion). Returns a copy with a
    ``priority_reason`` column.
    """
    reasons = priority_reasonings()
    parts: list[pd.DataFrame] = []

    review = subset[subset["classification"] == CLASS_NEEDS_REVIEW].sort_values(
        ["capability_evidence_score", "data_completeness_score", "unique_id"],
        ascending=[False, False, True],
    )
    parts.append(review.assign(priority_reason=reasons["review"]))

    trusted = subset[subset["classification"] == CLASS_TRUSTED]
    flagged = trusted[trusted["n_validation_flags"] > 0].sort_values(
        ["n_validation_flags", "unique_id"], ascending=[False, True]
    )
    parts.append(flagged.assign(priority_reason=reasons["trusted_flagged"]))
    clean = trusted[trusted["n_validation_flags"] == 0].sort_values(
        ["capability_evidence_score", "unique_id"], ascending=[False, True]
    )
    parts.append(clean.assign(priority_reason=reasons["trusted"]))

    if region_status == REGION_PLANNING_GAP:
        gaps = subset[subset["classification"] == CLASS_LIKELY_GAP].sort_values(
            ["data_completeness_score", "unique_id"], ascending=[False, True]
        )
        parts.append(gaps.assign(priority_reason=reasons["gap"]))

    if not parts:
        return subset.head(0).assign(priority_reason="")
    merged = pd.concat(parts).drop_duplicates("unique_id")
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


def example_regions(region_district: pd.DataFrame) -> dict[str, tuple[str, str]]:
    """Up to three deterministic demo selections from the CURRENT data.

    Picks, per showcased regional status, the district with the most
    records (ties broken by name) so the example never hard-codes a region
    that may vanish in another snapshot. Returns {label: (state, district)}.
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
        if len(rows):
            first = rows.iloc[0]
            examples[label] = (str(first["state"]), str(first["district"]))
    return examples
