"""Planner-facing regional guidance, decision path and evidence-policy copy.

Pure presentation logic (D24): everything here CONSUMES the existing
regional summaries and configured thresholds - no classification rule
lives in this module, and nothing here feeds scoring or aggregation.
Centralised so the UI copy is testable and consistent.
"""

from __future__ import annotations

from pydantic import BaseModel

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


class RegionalGuidance(BaseModel):
    """One-glance interpretation of a regional status for an NGO planner."""

    icon: str
    status: str
    meaning: str
    action: str


REGIONAL_GUIDANCE: dict[str, RegionalGuidance] = {
    REGION_TRUSTED: RegionalGuidance(
        icon="🟢",
        status=REGION_TRUSTED,
        meaning=(
            "At least one supplied record contains strong, corroborated ICU "
            "evidence. This confirms that trusted evidence exists in the "
            "dataset; it does not show that ICU capacity is adequate for the "
            "population."
        ),
        action=(
            "Inspect the trusted facilities and verify operational details "
            "before using the result for capacity or funding decisions."
        ),
    ),
    REGION_NEEDS_REVIEW: RegionalGuidance(
        icon="🟡",
        status=REGION_NEEDS_REVIEW,
        meaning="ICU-related claims exist, but none is currently safe to trust without review.",
        action=(
            "Review the flagged facilities before treating this region as "
            "either covered or a potential gap."
        ),
    ),
    REGION_PLANNING_GAP: RegionalGuidance(
        icon="🔴",
        status=REGION_PLANNING_GAP,
        meaning=(
            "The records are sufficiently populated to assess, but none "
            "contains credible ICU evidence."
        ),
        action=(
            "Flag this district for field verification or planning follow-up. "
            "This is not proof that no ICU exists."
        ),
    ),
    REGION_DATA_DESERT: RegionalGuidance(
        icon="⚪",
        status=REGION_DATA_DESERT,
        meaning="The available records are too sparse or incomplete to assess ICU evidence.",
        action="Prioritize data collection. Do not classify this region as an ICU gap.",
    ),
}


def regional_guidance(status: str) -> RegionalGuidance:
    """Guidance for a regional status; unknown values degrade safely."""
    known = REGIONAL_GUIDANCE.get(status)
    if known is not None:
        return known
    return RegionalGuidance(
        icon="⚪",
        status=status,
        meaning="Unrecognised regional status.",
        action="Inspect the underlying records before acting on this region.",
    )


# ---------------------------------------------------------------------------
# Visual decision path ("Why this status?")
# ---------------------------------------------------------------------------


class DecisionStep(BaseModel):
    """One step of the regional decision path, using existing values only."""

    question: str
    icon: str  # ✅ / ❌ / ⚠️ / status icon on the final step
    outcome: str
    detail: str  # threshold explanation for the tooltip / advanced view


def decision_path(summary: dict, config: ScoringConfig | None = None) -> list[DecisionStep]:
    """Mirror the regional classification precedence step by step.

    Consumes only the existing summary values (facility count, judgeable
    percentage, trusted count, needs-review count, stored regional status)
    and the configured thresholds - no new score is invented, and the
    final step always states the STORED regional status.
    """
    config = config or ScoringConfig()
    t = config.thresholds
    steps: list[DecisionStep] = []

    n = int(summary.get("facility_count", 0))
    enough_records = n >= t.region_min_facilities
    steps.append(
        DecisionStep(
            question="Enough records?",
            icon="✅" if enough_records else "❌",
            outcome=f"{n} record{'s' if n != 1 else ''}",
            detail=(
                f"At least {t.region_min_facilities} supplied records are required "
                "to assess a region; below that it is a data gap, not a medical gap."
            ),
        )
    )

    if enough_records:
        pct = float(summary.get("pct_sufficient_data", 0.0))
        enough_judgeable = pct >= t.region_min_data_pct
        steps.append(
            DecisionStep(
                question="Enough judgeable data?",
                icon="✅" if enough_judgeable else "❌",
                outcome=f"{pct:.0f}% judgeable",
                detail=(
                    f"Below {t.region_min_data_pct:.0f}% judgeable records the region "
                    "cannot be assessed reliably and is treated as insufficient data."
                ),
            )
        )

        if enough_judgeable:
            trusted = int(summary.get("trusted_icu_count", 0))
            found = trusted >= t.region_min_trusted
            steps.append(
                DecisionStep(
                    question="Trusted ICU evidence found?",
                    icon="✅" if found else "❌",
                    outcome=f"{trusted} record{'s' if trusted != 1 else ''}" if found else "None",
                    detail=(
                        f"At least {t.region_min_trusted} record meeting the Trusted "
                        "evidence standard marks the region as having trusted evidence "
                        "- evidence presence, never coverage adequacy."
                    ),
                )
            )

            review = int(summary.get("needs_review_count", 0))
            steps.append(
                DecisionStep(
                    question="Unresolved ICU claims?",
                    icon="⚠️" if review else "✅",
                    outcome=f"{review} record{'s' if review != 1 else ''}"
                    if review
                    else "None remaining",
                    detail=(
                        "Records with unverified or uncorroborated ICU claims block a "
                        "planning-gap conclusion and form the human-review worklist."
                    ),
                )
            )

    guidance = regional_guidance(str(summary.get("region_status", "")))
    steps.append(
        DecisionStep(
            question="Regional result",
            icon=guidance.icon,
            outcome=guidance.status,
            detail=guidance.meaning,
        )
    )
    return steps


# ---------------------------------------------------------------------------
# Reviewer actions per facility evidence status
# ---------------------------------------------------------------------------

FACILITY_REVIEWER_ACTIONS: dict[str, str] = {
    CLASS_TRUSTED: (
        "Verify operational details (beds, staffing, admission process) before "
        "relying on this record for planning."
    ),
    CLASS_NEEDS_REVIEW: (
        "Read the exact fragments and validator findings below, then record a "
        "reviewer note with your verdict."
    ),
    CLASS_LIKELY_GAP: (
        "No credible ICU evidence in this record - confirm the organization type "
        "before counting it toward a regional gap."
    ),
    CLASS_INSUFFICIENT: (
        "Too little data to judge - request or collect the missing fields before "
        "drawing any conclusion."
    ),
}


def reviewer_action(classification: str) -> str:
    return FACILITY_REVIEWER_ACTIONS.get(
        classification, "Inspect the supplied record before acting on it."
    )


# ---------------------------------------------------------------------------
# Evidence policy (versioned settings, not planner preferences)
# ---------------------------------------------------------------------------

EVIDENCE_POLICY_TITLE = "Evidence policy v1"

EVIDENCE_POLICY_CAPTION = (
    "These are versioned evidence-policy settings, not planner preferences. "
    "Changing them requires reclassification and evaluation; they are not "
    "adjustable in the normal planning interface."
)


def evidence_policy_lines(config: ScoringConfig | None = None) -> list[str]:
    """The complete Trusted rule plus the active thresholds, for display."""
    config = config or ScoringConfig()
    t = config.thresholds
    return [
        "A record can be **Trusted** only when:",
        "- it is judgeable (completeness ≥ " f"{t.sufficient_completeness});",
        f"- its evidence score reaches the trust threshold ({t.high_evidence});",
        "- it contains an explicit ICU claim;",
        f"- it has at least {t.min_corroboration_categories} distinct corroborating "
        "evidence categories;",
        "- it has no contradiction or blocking suspicious flag.",
        "",
        f"Judgeability threshold: **{t.sufficient_completeness}** · "
        f"low-evidence threshold: **{t.low_evidence}** · "
        f"regional minimum records: **{t.region_min_facilities}** · "
        f"regional judgeability threshold: **{t.region_min_data_pct:.0f}%**",
    ]
