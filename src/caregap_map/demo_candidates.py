"""Demo-candidate finder: safe, pre-vetted records for the live demo.

Pure selection over the processed outputs - classifications are never
changed. The full output contains real facility names/IDs, so the script
writes it under reports/ (git-ignored); only the code and aggregate-safe
documentation are committed.
"""

from __future__ import annotations

import json

import pandas as pd

from .config import (
    CLASS_NEEDS_REVIEW,
    CLASS_TRUSTED,
    REGION_DATA_DESERT,
    REGION_PLANNING_GAP,
    REGION_TRUSTED,
    SUBTYPE_GENERAL,
)

# Known persistent-store test data (live acceptance artefacts).
ACCEPTANCE_FACILITY_NOTE_ID = "1d947d78-19d6-485f-b8b7-20224e4c979c"
ACCEPTANCE_DISTRICT_SCOPE = "Maharashtra/Raigad"

SPECIALISED_SUBTYPES = {
    "nicu_only": "neonatal_icu",
    "picu_only": "pediatric_icu",
    "iccu_cardiac_only": "cardiac_icu",
    "micu_only": "medical_icu",
    "sicu_only": "surgical_icu",
}


def _facility(row: pd.Series, reason: str) -> dict:
    return {
        "name": row.get("name"),
        "unique_id": row.get("unique_id"),
        "state": row.get("state_final"),
        "district": row.get("district_final"),
        "classification": row.get("classification"),
        "subtypes": json.loads(row.get("icu_subtypes_json") or "[]"),
        "evidence_score": int(row.get("capability_evidence_score", 0)),
        "completeness_score": int(row.get("data_completeness_score", 0)),
        "reason": reason,
    }


def _district(row: pd.Series, reason: str) -> dict:
    return {
        "state": row.get("state"),
        "district": row.get("district"),
        "region_status": row.get("region_status"),
        "facility_count": int(row.get("facility_count", 0)),
        "trusted_count": int(row.get("trusted_icu_count", 0)),
        "pct_judgeable": float(row.get("pct_sufficient_data", 0.0)),
        "reason": reason,
    }


def find_demo_candidates(
    scored: pd.DataFrame, region_district: pd.DataFrame, per_category: int = 3
) -> dict[str, list[dict]]:
    """Deterministic demo candidates per category. Read-only."""
    out: dict[str, list[dict]] = {}
    subtype_lists = scored["icu_subtypes_json"].fillna("[]").map(json.loads)

    trusted = scored[scored["classification"] == CLASS_TRUSTED]
    general = trusted[
        subtype_lists.loc[trusted.index].map(lambda subs: SUBTYPE_GENERAL in subs)
    ].sort_values(["capability_evidence_score", "unique_id"], ascending=[False, True])
    out["trusted_general_icu"] = [
        _facility(r, "Trusted with a general/unspecified ICU claim and strong corroboration")
        for _, r in general.head(per_category).iterrows()
    ]

    review = scored[scored["classification"] == CLASS_NEEDS_REVIEW].sort_values(
        ["capability_evidence_score", "unique_id"], ascending=[False, True]
    )
    out["needs_human_review"] = [
        _facility(r, "High-signal but uncorroborated/ambiguous - shows the review worklist")
        for _, r in review.head(per_category).iterrows()
    ]

    for key, subtype in SPECIALISED_SUBTYPES.items():
        only = scored[subtype_lists.map(lambda subs, s=subtype: subs == [s])]
        only = only.sort_values(["capability_evidence_score", "unique_id"], ascending=[False, True])
        out[key] = [
            _facility(
                r,
                f"Only {subtype} evidence - drilldown shows the 'no general adult ICU claim' warning",
            )
            for _, r in only.head(per_category).iterrows()
        ]

    deserts = region_district[
        (region_district["region_status"] == REGION_DATA_DESERT)
        & (region_district["facility_count"] > 0)
    ].sort_values("facility_count", ascending=False)
    out["data_desert_district"] = [
        _district(r, "Records exist but are too thin to judge - unknown, not a gap")
        for _, r in deserts.head(per_category).iterrows()
    ]

    gaps = region_district[region_district["region_status"] == REGION_PLANNING_GAP].sort_values(
        "facility_count", ascending=False
    )
    out["planning_gap_district"] = [
        _district(r, "Judgeable records, none with credible ICU evidence")
        for _, r in gaps.head(per_category).iterrows()
    ]

    single = region_district[
        (region_district["region_status"] == REGION_TRUSTED)
        & (region_district["trusted_icu_count"] == 1)
    ].sort_values("facility_count", ascending=False)
    out["single_trusted_record_district"] = [
        _district(r, "Regional trust hinges on ONE record - fragility talking point")
        for _, r in single.head(per_category).iterrows()
    ]

    out["persistence_test_data"] = [
        {
            "scope": "facility",
            "unique_id": ACCEPTANCE_FACILITY_NOTE_ID,
            "reason": "Carries the live acceptance facility note",
        },
        {
            "scope": "district",
            "unique_id": ACCEPTANCE_DISTRICT_SCOPE,
            "reason": "Carries the live acceptance district note and the keep-scenario",
        },
    ]
    return out


def render_markdown(candidates: dict[str, list[dict]]) -> str:
    """reports/demo_facilities.md (git-ignored: real names + IDs)."""
    lines = [
        "# Demo facility candidates",
        "",
        "> Selection is read-only and never changes classifications.",
        "> This file contains real facility identifiers - keep it git-ignored.",
        "",
    ]
    for category, rows in candidates.items():
        lines.append(f"## {category}")
        lines.append("")
        if not rows:
            lines.append("- (no candidates found)")
        for row in rows:
            if "classification" in row:
                lines.append(
                    f"- **{row['name']}** `{row['unique_id']}` - {row['state']} / "
                    f"{row['district']} - {row['classification']} "
                    f"(subtypes: {', '.join(row['subtypes']) or 'none'}; evidence "
                    f"{row['evidence_score']}, completeness {row['completeness_score']})  \n"
                    f"  {row['reason']}"
                )
            elif "region_status" in row:
                lines.append(
                    f"- **{row['state']} / {row['district']}** - {row['region_status']} "
                    f"({row['facility_count']} records, {row['trusted_count']} trusted, "
                    f"{row['pct_judgeable']:.0f}% judgeable)  \n  {row['reason']}"
                )
            else:
                lines.append(f"- {row['scope']}: `{row['unique_id']}` - {row['reason']}")
        lines.append("")
    return "\n".join(lines)
