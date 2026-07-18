"""Regional aggregation of facility classifications.

A region is never labelled a medical desert just because records are
missing: evidence coverage (what the facilities show) and data coverage
(how judgeable the records are) are reported as separate metrics, and the
regional status distinguishes *Likely Medical Gap* from a *Data Desert*.
"""

from __future__ import annotations

import pandas as pd

from .config import (
    CLASS_INSUFFICIENT,
    CLASS_LIKELY_GAP,
    CLASS_NEEDS_REVIEW,
    CLASS_TRUSTED,
    REGION_DATA_DESERT,
    ScoringConfig,
)

UNASSIGNED = "(unassigned)"


def classify_region(
    facility_count: int,
    trusted_count: int,
    needs_review_count: int,
    pct_sufficient_data: float,
    config: ScoringConfig,
) -> tuple[str, str]:
    """Derive a regional status from facility-level results.

    Order of precedence: too little usable data -> Data Desert; any trusted
    facility -> Trusted Coverage; unresolved reviews -> Needs Human Review;
    otherwise the region shows well-documented absence -> Likely Medical Gap.
    """
    t = config.thresholds
    if facility_count < t.region_min_facilities:
        return (
            REGION_DATA_DESERT,
            f"Only {facility_count} facility record(s) - too few to judge coverage; "
            "this is a data gap, not a confirmed medical gap.",
        )
    if pct_sufficient_data < t.region_min_data_pct:
        return (
            REGION_DATA_DESERT,
            f"Only {pct_sufficient_data:.0f}% of records are judgeable "
            f"(threshold {t.region_min_data_pct:.0f}%); the region cannot be assessed reliably.",
        )
    if trusted_count >= t.region_min_trusted:
        return (
            CLASS_TRUSTED,
            f"{trusted_count} facility record(s) with trusted ICU evidence.",
        )
    if needs_review_count > 0:
        return (
            CLASS_NEEDS_REVIEW,
            f"No trusted ICU facility, but {needs_review_count} record(s) are ambiguous - "
            "verify them before calling this an ICU desert.",
        )
    return (
        CLASS_LIKELY_GAP,
        "Records are judgeable and none show credible ICU evidence - likely a real coverage gap.",
    )


def aggregate_regions(
    scored: pd.DataFrame,
    level: str,
    config: ScoringConfig | None = None,
) -> pd.DataFrame:
    """Aggregate scored facilities to ``state`` or ``district`` level.

    Requires the columns produced by the pipeline: ``state_final``,
    ``district_final``, ``classification``, ``capability_evidence_score``,
    ``data_completeness_score``. Facilities without a resolved region are
    kept under ``(unassigned)`` so nothing disappears silently.
    """
    config = config or ScoringConfig()
    df = scored.copy()
    df["state_key"] = df["state_final"].fillna(UNASSIGNED)
    if level == "state":
        keys = ["state_key"]
    elif level == "district":
        df["district_key"] = df["district_final"].fillna(UNASSIGNED)
        keys = ["state_key", "district_key"]
    else:
        raise ValueError(f"Unknown aggregation level: {level!r}")

    t = config.thresholds
    sufficient = df["data_completeness_score"] >= t.sufficient_completeness

    grouped = df.assign(_sufficient=sufficient).groupby(keys, dropna=False)
    rows = []
    for group_keys, g in grouped:
        group_keys = group_keys if isinstance(group_keys, tuple) else (group_keys,)
        counts = g["classification"].value_counts()
        n = len(g)
        trusted = int(counts.get(CLASS_TRUSTED, 0))
        likely_gap = int(counts.get(CLASS_LIKELY_GAP, 0))
        insufficient = int(counts.get(CLASS_INSUFFICIENT, 0))
        needs_review = int(counts.get(CLASS_NEEDS_REVIEW, 0))
        pct_sufficient = 100.0 * g["_sufficient"].mean() if n else 0.0

        # Trust-weighted coverage: evidence weighted by data confidence, so a
        # poorly documented "ICU" claim moves the needle less than a well
        # documented one. Range 0-1.
        weights = g["data_completeness_score"] / 100.0
        if weights.sum() > 0:
            trust_weighted = float(
                ((g["capability_evidence_score"] / 100.0) * weights).sum() / weights.sum()
            )
        else:
            trust_weighted = 0.0

        status, reason = classify_region(n, trusted, needs_review, pct_sufficient, config)
        row = dict(zip(keys, group_keys))
        row.update(
            {
                "facility_count": n,
                "trusted_icu_count": trusted,
                "likely_gap_count": likely_gap,
                "insufficient_data_count": insufficient,
                "needs_review_count": needs_review,
                "pct_sufficient_data": round(pct_sufficient, 1),
                "evidence_coverage_pct": round(100.0 * trusted / n, 1) if n else 0.0,
                "data_coverage_pct": round(pct_sufficient, 1),
                "trust_weighted_icu_coverage": round(trust_weighted, 3),
                "region_status": status,
                "region_status_reason": reason,
            }
        )
        rows.append(row)

    out = pd.DataFrame(rows)
    rename = {"state_key": "state"}
    if level == "district":
        rename["district_key"] = "district"
    return out.rename(columns=rename).sort_values(
        list(rename.values()), ignore_index=True
    )
