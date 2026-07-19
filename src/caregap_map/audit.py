"""Headline-metric audit: what the displayed numbers actually contain.

Answers, reproducibly, the questions a reviewer should ask before quoting
the app's headline figures:

- Why are ~99% of records "judgeable"? Which completeness components get
  them over the threshold, and how many pass only on upstream-generated
  text fields?
- Who is inside the facility-level "no ICU evidence" bucket? How many of
  those records are even the kind of organization one would expect to run
  an ICU (audit categorization by name - NOT clinical truth)?
- How solid are the Trusted records - corroboration, source fields,
  suspicious flags, boundary cases?
- What do the regional statuses rest on - how many districts hinge on a
  single trusted record, how many are dominated by non-hospital records?

Everything here is a DATASET-EVIDENCE analysis of the supplied records.
It is not a healthcare-access survey and proves nothing about real-world
ICU availability. Outputs containing record identifiers or excerpts belong
under reports/ (git-ignored), never in the repository.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

import pandas as pd

from .aggregation import aggregate_regions
from .config import (
    CLASS_INSUFFICIENT,
    CLASS_LIKELY_GAP,
    CLASS_NEEDS_REVIEW,
    CLASS_TRUSTED,
    REGION_DATA_DESERT,
    REGION_NEEDS_REVIEW,
    REGION_PLANNING_GAP,
    REGION_TRUSTED,
    ScoringConfig,
)

AUDIT_DISCLAIMER = (
    "This audit analyses the supplied dataset's records and the pipeline's "
    "own scores. It is a dataset-evidence analysis, not a real healthcare-"
    "access survey: nothing here verifies real-world ICU availability."
)

# ---------------------------------------------------------------------------
# Conservative audit categorization by facility name
# ---------------------------------------------------------------------------
# organization_type is uninformative in the supplied data (>99% literally
# "facility"), so the only broad-type signal is the facility NAME. These
# rules are deliberately conservative and exist for AUDIT REPORTING ONLY:
# they must never be used as clinical truth or to reclassify records.

CAT_HOSPITAL = "hospital_like"
CAT_CLINIC = "clinic_or_health_center"
CAT_PHARMACY = "pharmacy"
CAT_DENTIST = "dentist"
CAT_DOCTOR = "individual_doctor"
CAT_DIAGNOSTICS = "diagnostics_or_lab"
CAT_UNKNOWN = "unknown"

AUDIT_CATEGORIES = [
    CAT_HOSPITAL,
    CAT_CLINIC,
    CAT_PHARMACY,
    CAT_DENTIST,
    CAT_DOCTOR,
    CAT_DIAGNOSTICS,
    CAT_UNKNOWN,
]

# Categories whose members would not normally be expected to operate an ICU.
# clinic_or_health_center is deliberately NOT in this list: community health
# centres and larger polyclinics are ambiguous, so they are reported as their
# own band rather than folded into "clearly non-hospital".
CLEARLY_NON_HOSPITAL = [CAT_PHARMACY, CAT_DENTIST, CAT_DOCTOR, CAT_DIAGNOSTICS]

# Order matters: the first matching rule wins. Dental before hospital so a
# "Dental Hospital" is reported as a dental facility; hospital before
# diagnostics so "X Hospital & Diagnostics" stays hospital-like.
_CATEGORY_RULES: list[tuple[str, re.Pattern]] = [
    (
        CAT_PHARMACY,
        re.compile(r"\b(pharmacy|pharmacies|chemist|medical store|drug\s?store|druggist)\b", re.I),
    ),
    (CAT_DENTIST, re.compile(r"\b(dental|dentist|dentistry|orthodontic|orthodontist)\b", re.I)),
    (
        CAT_HOSPITAL,
        re.compile(
            r"\b(hospitals?|nursing home|medical college|institute of medical|"
            r"multi\s?special(i?ty|ities)|super\s?special(i?ty|ities)|sanatorium)\b",
            re.I,
        ),
    ),
    (
        CAT_DIAGNOSTICS,
        re.compile(
            r"\b(diagnostics?|labs?|laborator(y|ies)|pathology|patholab|scans?|scanning|"
            r"imaging|x-?rays?|radiology|mri|sonography|ultrasound)\b",
            re.I,
        ),
    ),
    (
        CAT_CLINIC,
        re.compile(
            r"\b(clinics?|polyclinics?|dispensar(y|ies)|health cent(er|re)s?|"
            r"primary health|community health|phc|chc)\b",
            re.I,
        ),
    ),
]

_DOCTOR_PREFIX = re.compile(r"^\s*(dr\.?|doctor)\s+", re.I)


def categorize_for_audit(name: Any, organization_type: Any = None) -> str:
    """Conservative broad-type guess from the facility name. AUDIT ONLY.

    Returns one of :data:`AUDIT_CATEGORIES`. Anything not clearly matched is
    ``unknown`` - the categorizer prefers under-claiming to over-claiming,
    and its output must never feed classification or clinical conclusions.
    """
    text = " ".join(
        str(v).strip() for v in (name, organization_type) if isinstance(v, str) and v.strip()
    )
    # The supplied organization_type is almost always the literal word
    # "facility", which carries no signal - drop it before matching.
    text = re.sub(r"\bfacility\b", " ", text, flags=re.I)
    if not text.strip():
        return CAT_UNKNOWN
    for category, pattern in _CATEGORY_RULES:
        if pattern.search(text):
            return category
    if _DOCTOR_PREFIX.search(text):
        return CAT_DOCTOR
    return CAT_UNKNOWN


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _json_col(series: pd.Series, default: str) -> pd.Series:
    return series.fillna(default).map(lambda s: json.loads(s or default))


def _score_stats(scores: pd.Series, bin_width: int = 10) -> dict:
    """Mean / median / percentiles / fixed-width histogram for a 0-100 score."""
    s = scores.astype(float)
    if s.empty:
        return {"count": 0}
    histogram = {}
    for lo in range(0, 100, bin_width):
        hi = lo + bin_width
        upper_inclusive = hi >= 100
        mask = (s >= lo) & ((s <= hi) if upper_inclusive else (s < hi))
        histogram[f"{lo}-{hi}"] = int(mask.sum())
    return {
        "count": int(s.count()),
        "mean": round(float(s.mean()), 2),
        "median": round(float(s.median()), 2),
        "percentiles": {
            f"p{p}": round(float(s.quantile(p / 100)), 1) for p in (5, 10, 25, 50, 75, 90, 95)
        },
        "histogram": histogram,
    }


def _fragment_groups(fragments_json: Any) -> set[str]:
    return {f.get("group") for f in json.loads(fragments_json or "[]")}


def _explicit_fields(fragments_json: Any) -> tuple[str, ...]:
    fragments = json.loads(fragments_json or "[]")
    return tuple(sorted({f.get("field") for f in fragments if f.get("group") == "explicit_icu"}))


# Completeness components produced by the upstream generative extraction pass
# (description/procedure/equipment were generated together from page content,
# see DECISIONS D18). staffing/capacity/source_url/geography are structural.
GENERATED_COMPONENTS = ("description", "procedure", "equipment")


# ---------------------------------------------------------------------------
# 1. Judgeability audit
# ---------------------------------------------------------------------------


def audit_judgeability(scored: pd.DataFrame, config: ScoringConfig | None = None) -> dict:
    """How does (nearly) every record reach the judgeability threshold?"""
    config = config or ScoringConfig()
    threshold = config.thresholds.sufficient_completeness
    n_total = len(scored)
    judgeable = scored[scored["data_completeness_score"] >= threshold]
    n_jud = len(judgeable)

    components = _json_col(judgeable["completeness_components_json"], "{}")
    combo_counts = Counter(tuple(sorted(c)) for c in components)
    combos = [
        {
            "components": list(combo),
            "count": count,
            "pct_of_judgeable": round(100.0 * count / n_jud, 1) if n_jud else 0.0,
        }
        for combo, count in combo_counts.most_common()
    ]

    groups = judgeable["evidence_fragments_json"].map(_fragment_groups)
    has_proc_field = components.map(lambda c: "procedure" in c)
    has_equip_field = components.map(lambda c: "equipment" in c)
    icu_proc = groups.map(lambda g: "procedure" in g)
    icu_equip = groups.map(lambda g: "equipment" in g)
    only_generic = (has_proc_field | has_equip_field) & ~icu_proc & ~icu_equip

    non_generated_score = components.map(
        lambda c: sum(v for k, v in c.items() if k not in GENERATED_COMPONENTS)
    )

    def _count(mask: pd.Series) -> dict:
        return {
            "count": int(mask.sum()),
            "pct_of_judgeable": round(100.0 * float(mask.mean()), 1) if n_jud else 0.0,
        }

    return {
        "threshold": threshold,
        "records_total": n_total,
        "records_judgeable": n_jud,
        "pct_judgeable_exact": round(100.0 * n_jud / n_total, 2) if n_total else 0.0,
        "among_judgeable": {
            "missing_capacity": _count(components.map(lambda c: "capacity" not in c)),
            "missing_number_doctors": _count(components.map(lambda c: "staffing" not in c)),
            "missing_source_url": _count(components.map(lambda c: "source_url" not in c)),
            "missing_valid_coordinates": _count(judgeable["coord_status"] != "ok"),
            "missing_resolved_district": _count(judgeable["district_final"].isna()),
            "no_icu_relevant_procedure": _count(~icu_proc),
            "no_icu_relevant_equipment": _count(~icu_equip),
            "no_explicit_icu_claim": _count(~judgeable["explicit_icu_claim"].astype(bool)),
            "only_generic_procedure_equipment_content": _count(only_generic),
            "judgeable_solely_from_generated_fields": _count(non_generated_score < threshold),
        },
        "component_combinations": combos,
        "completeness_score_distribution": _score_stats(scored["data_completeness_score"]),
        "evidence_score_distribution": _score_stats(scored["capability_evidence_score"]),
        "interpretation": (
            "Judgeability measures whether the supplied record's fields are populated - "
            "NOT whether their content is informative for ICU assessment. "
            f"description ({config.completeness_weights.description}) + geography "
            f"({config.completeness_weights.geography}) + source_url "
            f"({config.completeness_weights.source_url}) alone already reach the "
            f"threshold of {threshold}."
        ),
    }


# ---------------------------------------------------------------------------
# 2. Facility-level gap-record audit
# ---------------------------------------------------------------------------


def audit_gap_records(scored: pd.DataFrame, config: ScoringConfig | None = None) -> dict:
    """Who is inside the facility-level 'no ICU evidence' bucket?"""
    config = config or ScoringConfig()
    gaps = scored[scored["classification"] == CLASS_LIKELY_GAP].copy()
    n = len(gaps)

    org_type = gaps.get("organization_type", pd.Series(dtype=object))
    org_counts = (
        org_type.fillna("(null)").map(lambda v: str(v)[:40]).value_counts().head(10).to_dict()
    )

    categories = pd.Series(
        [categorize_for_audit(nm, ot) for nm, ot in zip(gaps.get("name"), org_type, strict=False)],
        index=gaps.index,
        dtype=object,
    )
    cat_counts = {c: int((categories == c).sum()) for c in AUDIT_CATEGORIES}
    clearly_non_hospital = int(categories.isin(CLEARLY_NON_HOSPITAL).sum())

    return {
        "gap_records_total": n,
        "by_organization_type_top10": org_counts,
        "facility_type_id_available": "facilityTypeId" in scored.columns,
        "by_audit_category": cat_counts,
        "hospital_like": {
            "count": cat_counts[CAT_HOSPITAL],
            "pct": round(100.0 * cat_counts[CAT_HOSPITAL] / n, 1) if n else 0.0,
        },
        "clearly_non_hospital": {
            "count": clearly_non_hospital,
            "pct": round(100.0 * clearly_non_hospital / n, 1) if n else 0.0,
            "categories": CLEARLY_NON_HOSPITAL,
        },
        "ambiguous_clinic_or_health_center": cat_counts[CAT_CLINIC],
        "uncategorizable": cat_counts[CAT_UNKNOWN],
        "by_state": gaps["state_final"].fillna("(unassigned)").value_counts().to_dict(),
        "by_district_top20": gaps["district_final"]
        .fillna("(unassigned)")
        .value_counts()
        .head(20)
        .to_dict(),
        "evidence_score_distribution": _score_stats(gaps["capability_evidence_score"]),
        "completeness_score_distribution": _score_stats(gaps["data_completeness_score"]),
        "with_source_url": int(
            _json_col(gaps["completeness_components_json"], "{}").map(lambda c: "source_url" in c).sum()
        ),
        "with_capacity": int(gaps["capacity_int"].notna().sum()) if "capacity_int" in gaps else None,
        "with_doctor_count": int(gaps["number_doctors_int"].notna().sum())
        if "number_doctors_int" in gaps
        else None,
        "category_caveat": (
            "Audit categories are conservative name-based guesses for reporting only - "
            "not clinical truth, and never used for classification. A judgeable "
            "pharmacy/dentist/lab record without ICU evidence is an expected absence, "
            "not a medical gap."
        ),
    }


# ---------------------------------------------------------------------------
# 3. Trusted-record audit
# ---------------------------------------------------------------------------


def audit_trusted_records(scored: pd.DataFrame, config: ScoringConfig | None = None) -> dict:
    """How solid are the records meeting the Trusted ICU evidence standard?"""
    config = config or ScoringConfig()
    t = config.thresholds
    trusted = scored[scored["classification"] == CLASS_TRUSTED].copy()
    n = len(trusted)
    if n == 0:
        return {"trusted_records_total": 0}

    subtype_counts: Counter = Counter()
    for subtypes in _json_col(trusted["icu_subtypes_json"], "[]"):
        for s in subtypes or ["(none)"]:
            subtype_counts[s] += 1

    corro_combo_counts = Counter(
        tuple(sorted(c)) for c in _json_col(trusted["corroboration_categories_json"], "[]")
    )
    explicit_field_counts = Counter(trusted["evidence_fragments_json"].map(_explicit_fields))

    ev_components = _json_col(trusted["evidence_components_json"], "{}")
    flags = _json_col(trusted["validation_flags_json"], "[]")
    flag_name_counts: Counter = Counter()
    for record_flags in flags:
        for f in record_flags:
            flag_name_counts[f.get("name")] += 1

    components = _json_col(trusted["completeness_components_json"], "{}")
    # Claim risk: every field is upstream-generated, but a claim appearing
    # ONLY in the structured list fields (never in the description prose) has
    # a higher chance of being an image-derived / single-pass artefact.
    structured_only = trusted["evidence_fragments_json"].map(
        lambda fj: _explicit_fields(fj) != () and "description" not in _explicit_fields(fj)
    )

    barely = trusted[
        (trusted["capability_evidence_score"] <= t.high_evidence + 5)
        | (trusted["n_corroboration_categories"] <= t.min_corroboration_categories)
    ]

    return {
        "trusted_records_total": n,
        "icu_subtype_distribution": dict(subtype_counts.most_common()),
        "corroboration_category_combinations": {
            " + ".join(combo) if combo else "(none)": count
            for combo, count in corro_combo_counts.most_common()
        },
        "explicit_claim_field_combinations": {
            " + ".join(combo) if combo else "(none)": count
            for combo, count in explicit_field_counts.most_common()
        },
        "with_cross_field_consistency_bonus": int(
            ev_components.map(lambda c: "cross_field_consistency" in c).sum()
        ),
        "explicit_claim_never_in_description": int(structured_only.sum()),
        "missing_source_url": int(components.map(lambda c: "source_url" not in c).sum()),
        "with_any_validation_flag": int((trusted["n_validation_flags"] > 0).sum()),
        "validation_flag_counts": dict(flag_name_counts.most_common()),
        "with_total_capacity": int(trusted["capacity_int"].notna().sum())
        if "capacity_int" in trusted
        else None,
        "with_doctor_count": int(trusted["number_doctors_int"].notna().sum())
        if "number_doctors_int" in trusted
        else None,
        "possible_duplicate_facilities": flag_name_counts.get("possible_duplicate_facility", 0),
        "directory_or_partner_content_flags": flag_name_counts.get(
            "directory_or_partner_content_detected", 0
        ),
        "boundary_cases": {
            "definition": (
                f"evidence score <= {t.high_evidence + 5} (threshold {t.high_evidence} + 5) "
                f"or corroboration at the minimum of {t.min_corroboration_categories}"
            ),
            "count": len(barely),
            "records": [
                {
                    "unique_id": r["unique_id"],
                    "evidence_score": int(r["capability_evidence_score"]),
                    "n_corroboration_categories": int(r["n_corroboration_categories"]),
                }
                for _, r in barely.sort_values("capability_evidence_score").iterrows()
            ],
        },
    }


# ---------------------------------------------------------------------------
# 4. Regional-consequence audit
# ---------------------------------------------------------------------------


def audit_regional(scored: pd.DataFrame, config: ScoringConfig | None = None) -> dict:
    """What do the regional statuses rest on?"""
    config = config or ScoringConfig()
    by_state = aggregate_regions(scored, "state", config)
    by_district = aggregate_regions(scored, "district", config)

    def status_counts(df: pd.DataFrame) -> dict:
        return {status: int((df["region_status"] == status).sum()) for status in (
            REGION_TRUSTED,
            REGION_NEEDS_REVIEW,
            REGION_PLANNING_GAP,
            REGION_DATA_DESERT,
        )}

    single_trusted = by_district[
        (by_district["region_status"] == REGION_TRUSTED) & (by_district["trusted_icu_count"] == 1)
    ]

    categories = pd.Series(
        [
            categorize_for_audit(nm, ot)
            for nm, ot in zip(scored.get("name"), scored.get("organization_type"), strict=False)
        ],
        index=scored.index,
        dtype=object,
    )
    non_hospital = categories.isin(CLEARLY_NON_HOSPITAL)
    keys = scored["state_final"].fillna("(unassigned)") + " / " + scored["district_final"].fillna(
        "(unassigned)"
    )
    dominance = non_hospital.groupby(keys).mean()
    dominated = dominance[dominance >= 0.5]

    return {
        "disclaimer": AUDIT_DISCLAIMER,
        "states_total": len(by_state),
        "state_status_counts": status_counts(by_state),
        "districts_total": len(by_district),
        "district_status_counts": status_counts(by_district),
        "district_data_deserts": int((by_district["region_status"] == REGION_DATA_DESERT).sum()),
        "district_potential_planning_gaps": int(
            (by_district["region_status"] == REGION_PLANNING_GAP).sum()
        ),
        "district_needing_verification": int(
            (by_district["region_status"] == REGION_NEEDS_REVIEW).sum()
        ),
        "district_with_trusted_evidence": int(
            (by_district["region_status"] == REGION_TRUSTED).sum()
        ),
        "districts_where_one_trusted_record_decides_status": {
            "count": len(single_trusted),
            "districts": [
                f"{r['state']} / {r['district']}" for _, r in single_trusted.iterrows()
            ],
        },
        "districts_dominated_by_non_hospital_records": {
            "definition": ">= 50% of the district's records are pharmacy/dentist/"
            "individual-doctor/diagnostics by the audit categorizer",
            "count": len(dominated),
            "top20": {
                k: round(float(v), 2)
                for k, v in dominated.sort_values(ascending=False).head(20).items()
            },
        },
    }


# ---------------------------------------------------------------------------
# Full report + markdown rendering
# ---------------------------------------------------------------------------


def build_audit_report(scored: pd.DataFrame, config: ScoringConfig | None = None) -> dict:
    """Assemble the complete headline-metric audit as one JSON-safe dict."""
    config = config or ScoringConfig()
    counts = scored["classification"].value_counts()
    return {
        "disclaimer": AUDIT_DISCLAIMER,
        "records_total": len(scored),
        "classification_counts": {
            CLASS_TRUSTED: int(counts.get(CLASS_TRUSTED, 0)),
            CLASS_NEEDS_REVIEW: int(counts.get(CLASS_NEEDS_REVIEW, 0)),
            CLASS_LIKELY_GAP: int(counts.get(CLASS_LIKELY_GAP, 0)),
            CLASS_INSUFFICIENT: int(counts.get(CLASS_INSUFFICIENT, 0)),
        },
        "judgeability": audit_judgeability(scored, config),
        "gap_records": audit_gap_records(scored, config),
        "trusted_records": audit_trusted_records(scored, config),
        "regional": audit_regional(scored, config),
    }


def _md_table(counts: dict, key_header: str, value_header: str = "count") -> list[str]:
    lines = [f"| {key_header} | {value_header} |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in counts.items()]
    return lines


def render_markdown(report: dict) -> str:
    """Human-readable version of :func:`build_audit_report` output."""
    jud = report["judgeability"]
    gap = report["gap_records"]
    trusted = report["trusted_records"]
    regional = report["regional"]

    lines = [
        "# Headline-metric audit",
        "",
        f"> {report['disclaimer']}",
        "",
        f"Records: **{report['records_total']}**",
        "",
    ]
    lines += _md_table(report["classification_counts"], "classification")

    lines += [
        "",
        "## 1. Judgeability",
        "",
        f"{jud['records_judgeable']} of {jud['records_total']} records "
        f"(**{jud['pct_judgeable_exact']}%**) meet the completeness threshold "
        f"of {jud['threshold']}.",
        "",
        jud["interpretation"],
        "",
        "### Among judgeable records",
        "",
        "| gap in the record | count | % of judgeable |",
        "|---|---|---|",
    ]
    for key, val in jud["among_judgeable"].items():
        lines.append(f"| {key.replace('_', ' ')} | {val['count']} | {val['pct_of_judgeable']}% |")
    lines += ["", "### Most common completeness-component combinations", ""]
    lines += ["| components | count | % of judgeable |", "|---|---|---|"]
    for combo in jud["component_combinations"][:15]:
        lines.append(
            f"| {', '.join(combo['components'])} | {combo['count']} | {combo['pct_of_judgeable']}% |"
        )
    stats = jud["completeness_score_distribution"]
    lines += [
        "",
        f"Completeness score: mean {stats.get('mean')}, median {stats.get('median')}; "
        f"histogram {stats.get('histogram')}",
    ]

    lines += [
        "",
        f"## 2. Facility-level records with no ICU evidence ({gap['gap_records_total']})",
        "",
        gap["category_caveat"],
        "",
    ]
    lines += _md_table(gap["by_audit_category"], "audit category (name-derived)")
    lines += [
        "",
        f"- hospital-like: **{gap['hospital_like']['count']}** ({gap['hospital_like']['pct']}%)",
        f"- clearly non-hospital (pharmacy/dentist/individual doctor/diagnostics): "
        f"**{gap['clearly_non_hospital']['count']}** ({gap['clearly_non_hospital']['pct']}%)",
        f"- ambiguous clinic / health centre: {gap['ambiguous_clinic_or_health_center']}",
        f"- not reliably categorizable: {gap['uncategorizable']}",
        f"- facilityTypeId column available: {gap['facility_type_id_available']}",
        f"- with source URL: {gap['with_source_url']}; with capacity: {gap['with_capacity']}; "
        f"with doctor count: {gap['with_doctor_count']}",
    ]

    lines += ["", f"## 3. Trusted records ({trusted.get('trusted_records_total', 0)})", ""]
    if trusted.get("trusted_records_total"):
        lines += _md_table(trusted["icu_subtype_distribution"], "ICU subtype")
        lines += [""]
        lines += _md_table(trusted["corroboration_category_combinations"], "corroboration categories")
        lines += [
            "",
            f"- explicit claim never appears in description prose: "
            f"{trusted['explicit_claim_never_in_description']}",
            f"- cross-field-consistency bonus used: {trusted['with_cross_field_consistency_bonus']}",
            f"- missing source URL: {trusted['missing_source_url']}",
            f"- records with any validation flag: {trusted['with_any_validation_flag']}",
            f"- possible duplicates: {trusted['possible_duplicate_facilities']}; "
            f"directory/partner-content flags: {trusted['directory_or_partner_content_flags']}",
            f"- with total capacity: {trusted['with_total_capacity']}; "
            f"with doctor count: {trusted['with_doctor_count']}",
            "",
            f"### Boundary cases ({trusted['boundary_cases']['count']})",
            "",
            trusted["boundary_cases"]["definition"],
            "",
        ]
        for r in trusted["boundary_cases"]["records"][:25]:
            lines.append(
                f"- `{r['unique_id']}` evidence {r['evidence_score']}, "
                f"corroboration {r['n_corroboration_categories']}"
            )

    lines += [
        "",
        "## 4. Regional consequences",
        "",
        f"> {regional['disclaimer']}",
        "",
        f"### States ({regional['states_total']})",
        "",
    ]
    lines += _md_table(regional["state_status_counts"], "regional status")
    lines += ["", f"### Districts ({regional['districts_total']})", ""]
    lines += _md_table(regional["district_status_counts"], "regional status")
    single = regional["districts_where_one_trusted_record_decides_status"]
    dominated = regional["districts_dominated_by_non_hospital_records"]
    lines += [
        "",
        f"- districts where a SINGLE trusted record decides the status: **{single['count']}**",
        f"- districts dominated by non-hospital records ({dominated['definition']}): "
        f"**{dominated['count']}**",
        "",
    ]
    return "\n".join(lines) + "\n"
