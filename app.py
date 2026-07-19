"""CareGap Map - ICU evidence for public-health planning.

Streamlit app organised around the NGO planner workflow (D24):
1 select region -> 2 understand the evidence -> 3 review priority
facilities -> 4 save a planning scenario. All semantics (scoring,
classification, aggregation) are consumed, never computed, here.
Run `python scripts/build_processed_data.py` first, then
`streamlit run app.py`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

SRC = Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from caregap_map.aggregation import UNASSIGNED, summarize_facilities  # noqa: E402
from caregap_map.cleaning import parse_list_field  # noqa: E402
from caregap_map.config import (  # noqa: E402
    CLASS_INSUFFICIENT,
    CLASS_LIKELY_GAP,
    CLASS_NEEDS_REVIEW,
    CLASS_TRUSTED,
    REGION_DATA_DESERT,
    REGION_DISCLAIMER,
    REGION_NEEDS_REVIEW,
    REGION_PLANNING_GAP,
    REGION_TRUSTED,
    SUBTYPE_GENERAL,
    SUBTYPE_LABELS,
    facility_display_label,
    load_env_file,
    load_scoring_config,
)

load_env_file()  # .env overrides nothing that is already in the environment
from caregap_map.data_access import MissingDataError, get_data_source  # noqa: E402
from caregap_map.persistence import ReviewNote, ReviewStore, get_review_store  # noqa: E402
from caregap_map.planning import (  # noqa: E402
    OPERATIONAL_COMPONENTS,
    OPERATIONAL_HELP,
    assess_operational_data,
    assessment_status,
)
from caregap_map.regional_guidance import (  # noqa: E402
    EVIDENCE_POLICY_CAPTION,
    EVIDENCE_POLICY_TITLE,
    decision_path,
    evidence_policy_lines,
    regional_guidance,
    reviewer_action,
)
from caregap_map.scenarios import (  # noqa: E402
    ScenarioStore,
    data_snapshot_id,
    get_scenario_store,
    scenario_from_summary,
    scoring_config_fingerprint,
)
from caregap_map.ui_components import (  # noqa: E402
    CONTEXT_CAPTION,
    MAP_MARKER,
    MIX_GAP_WARNING,
    district_centroids,
    example_regions,
    facility_mix_counts,
    facility_mix_sentence,
    hero_counts_html,
    map_view,
    mix_warning_applies,
    primary_flag,
    select_priority_facilities,
    status_distribution,
)
from caregap_map.ui_state import desired_region_params, normalize_region_request  # noqa: E402
from caregap_map.workflow import (  # noqa: E402
    current_step_label,
    infer_workflow_state,
    sidebar_workflow_html,
)

# Status colors (validated with the dataviz palette checker, severity order:
# trusted -> review -> gap -> no data so green/red are never adjacent).
CLASS_COLORS = {
    CLASS_TRUSTED: "#0ca30c",  # status: good
    CLASS_NEEDS_REVIEW: "#fab219",  # status: warning
    CLASS_LIKELY_GAP: "#d03b3b",  # status: critical
    CLASS_INSUFFICIENT: "#898781",  # deliberately gray: absence of data, not a status
}
CLASS_STACK_ORDER = [CLASS_TRUSTED, CLASS_NEEDS_REVIEW, CLASS_LIKELY_GAP, CLASS_INSUFFICIENT]
CLASS_ICONS = {
    CLASS_TRUSTED: "🟢",
    CLASS_NEEDS_REVIEW: "🟡",
    CLASS_LIKELY_GAP: "🔴",
    CLASS_INSUFFICIENT: "⚪",
    REGION_TRUSTED: "🟢",
    REGION_NEEDS_REVIEW: "🟡",
    REGION_PLANNING_GAP: "🔴",
    REGION_DATA_DESERT: "⚪",
}
# Chip backgrounds pair each status color with a text color that keeps
# WCAG-adequate contrast in BOTH Streamlit themes (chips are solid, so the
# page theme does not affect their internal contrast). Status is always
# icon + label + text, never color alone.
CHIP_STYLE = {
    REGION_TRUSTED: ("#0a7d0a", "#ffffff"),
    REGION_NEEDS_REVIEW: ("#fab219", "#1a1a1a"),
    REGION_PLANNING_GAP: ("#c22f2f", "#ffffff"),
    REGION_DATA_DESERT: ("#5f5d58", "#ffffff"),
    CLASS_TRUSTED: ("#0a7d0a", "#ffffff"),
    CLASS_NEEDS_REVIEW: ("#fab219", "#1a1a1a"),
    CLASS_LIKELY_GAP: ("#c22f2f", "#ffffff"),
    CLASS_INSUFFICIENT: ("#5f5d58", "#ffffff"),
}

REGION_STATUS_ORDER = [REGION_TRUSTED, REGION_NEEDS_REVIEW, REGION_PLANNING_GAP, REGION_DATA_DESERT]
# Regional statuses reuse the validated class palette; gray for data deserts
# is essential - without it the map would visually collapse data deserts
# into medical gaps, the exact mistake CareGap Map is designed to prevent.
REGION_COLORS = {
    REGION_TRUSTED: CLASS_COLORS[CLASS_TRUSTED],
    REGION_NEEDS_REVIEW: CLASS_COLORS[CLASS_NEEDS_REVIEW],
    REGION_PLANNING_GAP: CLASS_COLORS[CLASS_LIKELY_GAP],
    REGION_DATA_DESERT: CLASS_COLORS[CLASS_INSUFFICIENT],
}

LANDSCAPE_CAPTION = (
    "Colors represent evidence status in the supplied dataset — not real-world "
    "ICU operation, population coverage, travel access or service capacity."
)

st.set_page_config(
    page_title="CareGap Map",
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="collapsed",
)

_CSS = """
<style>
/* Streamlit's app header is a ~3.75rem sticky overlay: the content needs
   at least that much top padding or the title renders underneath it. */
.block-container {padding-top: 4.25rem; padding-bottom: 3rem;}
.cg-title {font-size: 1.9rem; font-weight: 800; line-height: 1.15; margin: 0;}
.cg-title a {color: inherit; text-decoration: none;}
.cg-title a:hover {opacity: .8; text-decoration: none;}
.cg-subtitle {font-size: 1.0rem; opacity: .78; margin: .1rem 0 .55rem 0;}
.cg-anchor {display: block; position: relative; visibility: hidden;
            scroll-margin-top: 4.5rem; height: 0;}
.cg-wf {display: flex; flex-direction: column; gap: .45rem; margin-bottom: 1rem;}
.cg-wf-step {display: flex; gap: .55rem; align-items: flex-start;
             border: 1px solid rgba(128,128,128,.35); border-radius: 10px;
             padding: .45rem .6rem; text-decoration: none !important; color: inherit;}
.cg-wf-step.current {border: 2px solid #1c83e1; background: rgba(28,131,225,.08);}
.cg-wf-step.current .cg-wf-title {font-weight: 750;}
.cg-wf-step.done {opacity: .72;}
.cg-wf-mark {min-width: 1rem; font-weight: 700; line-height: 1.35;}
.cg-wf-title {display: block; font-weight: 600; font-size: .92rem; color: inherit;}
.cg-wf-desc {display: block; font-size: .8rem; opacity: .78; font-weight: 400;}
.cg-chip {display: inline-block; border-radius: 999px; padding: .28rem .85rem;
          font-weight: 750; font-size: 1.08rem; letter-spacing: .01em;}
.cg-counts {font-size: .95rem; opacity: .92; margin-top: .35rem;}
h1, h2, h3 {letter-spacing: -.01em;}
div[data-testid="stMetricValue"] {font-size: 1.6rem;}
</style>
"""


@st.cache_data(show_spinner="Loading processed data ...")
def load_data():
    source = get_data_source()
    scored = source.load_scored_facilities()
    region_state = source.load_region_summary("state")
    region_district = source.load_region_summary("district")
    return scored, region_state, region_district


@st.cache_resource
def note_store() -> ReviewStore:
    # CAREGAP_REVIEW_STORE=sqlite (local default) | databricks (Delta table,
    # survives app restarts - required for the deployed app).
    return get_review_store()


@st.cache_resource
def scenario_store() -> ScenarioStore:
    # Follows CAREGAP_SCENARIO_STORE, falling back to CAREGAP_REVIEW_STORE.
    return get_scenario_store()


def status_chip(status: str) -> str:
    """Solid status chip: icon + label, never color alone."""
    bg, fg = CHIP_STYLE.get(status, ("#5f5d58", "#ffffff"))
    icon = CLASS_ICONS.get(status, "⚪")
    label = facility_display_label(status)
    return (
        f'<span class="cg-chip" style="background:{bg}; color:{fg};">'
        f"{icon}&nbsp;{label.upper()}</span>"
    )


def anchor(key: str) -> None:
    """Stable in-page navigation target for the sidebar workflow links."""
    st.markdown(f'<div id="{key}" class="cg-anchor"></div>', unsafe_allow_html=True)


def request_scroll(anchor_id: str) -> None:
    """Ask the NEXT render to scroll to an anchor (used by click handlers)."""
    st.session_state["scroll_to"] = anchor_id


def flush_scroll() -> None:
    """One-shot smooth scroll to a requested anchor after a rerun.

    The only scripted behavior in the app: Streamlit has no scroll API, so
    a click that should land the user somewhere (e.g. "Review evidence" ->
    the facility review) emits this zero-height helper once. It navigates
    nothing and touches no state or URL.
    """
    target = st.session_state.pop("scroll_to", None)
    if not target:
        return
    components.html(
        f"""<script>
        const el = window.parent.document.getElementById({target!r});
        if (el) {{ el.scrollIntoView({{behavior: "smooth", block: "start"}}); }}
        </script>""",
        height=0,
    )


def classification_chart(df: pd.DataFrame, group_col: str, title: str) -> None:
    """Stacked horizontal bar: facility classifications per region."""
    counts = (
        df.groupby([group_col, "classification"])
        .size()
        .unstack(fill_value=0)
        .reindex(columns=CLASS_STACK_ORDER, fill_value=0)
    )
    counts = counts.loc[counts.sum(axis=1).sort_values(ascending=True).index]
    if len(counts) > 30:
        counts = counts.tail(30)
        st.caption(f"Showing the 30 largest of {df[group_col].nunique()} regions by record count.")

    theme = getattr(getattr(st, "context", None), "theme", None)
    surface = "#0e1117" if theme is not None and theme.type == "dark" else "#ffffff"

    fig = go.Figure()
    for cls in CLASS_STACK_ORDER:
        label = facility_display_label(cls)
        fig.add_trace(
            go.Bar(
                y=counts.index,
                x=counts[cls],
                name=label,
                orientation="h",
                marker={"color": CLASS_COLORS[cls], "line": {"color": surface, "width": 2}},
                hovertemplate=f"%{{y}}<br>{label}: %{{x}} facility records<extra></extra>",
            )
        )
    fig.update_layout(
        barmode="stack",
        title=title,
        height=max(300, 24 * len(counts) + 120),
        margin={"l": 10, "r": 10, "t": 50, "b": 10},
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.0, "xanchor": "left", "x": 0},
        xaxis_title="facility records",
        yaxis_title=None,
    )
    st.plotly_chart(fig, width="stretch")


def distribution_bar(subset: pd.DataFrame) -> None:
    """100% stacked horizontal evidence-status bar with counts + percentages."""
    rows = status_distribution(subset)
    if not any(r["count"] for r in rows):
        return
    fig = go.Figure()
    for r in rows:
        if not r["count"]:
            continue
        fig.add_trace(
            go.Bar(
                y=["Evidence status"],
                x=[r["count"]],
                name=r["label"],
                orientation="h",
                marker={"color": CLASS_COLORS[r["classification"]]},
                text=f"{r['count']}" if r["pct"] >= 6 else "",
                textposition="inside",
                hovertemplate=(
                    f"{r['label']}: {r['count']} records ({r['pct']}%)<extra></extra>"
                ),
            )
        )
    fig.update_layout(
        barmode="stack",
        height=86,
        margin={"l": 0, "r": 0, "t": 0, "b": 0},
        showlegend=True,
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "font": {"size": 11}},
        xaxis={"visible": False},
        yaxis={"visible": False},
    )
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})


def facility_map(subset: pd.DataFrame) -> str | None:
    """Facility point map; returns a map-selected unique_id, if any.

    Deliberately a POINT map, not a choropleth: it shows where evidence-
    carrying records sit, and must not imply population-adjusted access or
    verified clinical capability. Facilities without valid coordinates are
    counted below the map and remain fully accessible in the table.
    """
    located = subset[subset["coord_status"] == "ok"].copy()
    unlocated = len(subset) - len(located)
    if located.empty:
        st.info("No facilities with valid coordinates in this selection - use the table.")
        return None

    located["status"] = located["classification"].map(
        lambda c: f"{CLASS_ICONS.get(c, '')} {facility_display_label(c)}"
    )
    located["city_disp"] = located["address_city"].fillna("-")
    located["district_disp"] = located["district_final"].fillna("-")
    order = [f"{CLASS_ICONS[c]} {facility_display_label(c)}" for c in CLASS_STACK_ORDER]
    colors = {
        f"{CLASS_ICONS[c]} {facility_display_label(c)}": CLASS_COLORS[c] for c in CLASS_STACK_ORDER
    }
    # Deterministic bounds-aware view: original coordinates are never
    # modified or jittered; overlapping records simply overlap.
    center_lat, center_lon, zoom = map_view(
        located["lat_parsed"].tolist(), located["lon_parsed"].tolist()
    )
    fig = px.scatter_map(
        located,
        lat="lat_parsed",
        lon="lon_parsed",
        color="status",
        category_orders={"status": order},
        color_discrete_map=colors,
        hover_name="name",
        custom_data=[
            "unique_id",
            "city_disp",
            "district_disp",
            "capability_evidence_score",
            "data_completeness_score",
        ],
        zoom=zoom,
        center={"lat": center_lat, "lon": center_lon},
        height=420,
    )
    hover = (
        "<b>%{hovertext}</b><br>%{customdata[1]}, %{customdata[2]}<br>STATUS<br>"
        "ICU evidence score: %{customdata[3]} / 100<br>"
        "Record judgeability: %{customdata[4]} / 100<br>"
        "<i>Click to open the evidence review</i><extra></extra>"
    )
    for trace in fig.data:
        trace.hovertemplate = hover.replace("STATUS", str(trace.name))
    fig.update_traces(marker=MAP_MARKER)
    fig.update_layout(
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.0},
        margin={"l": 0, "r": 0, "t": 10, "b": 0},
        map_style="open-street-map",
    )
    event = st.plotly_chart(
        fig, width="stretch", on_select="rerun", selection_mode="points", key="facility_map"
    )
    st.caption(
        f"{len(located)} located facility records; **{unlocated} without valid coordinates "
        "are NOT on this map** but appear in the table below. Some records may share or "
        "nearly share coordinates, so markers can overlap. Points show record locations "
        "and evidence status only - not travel time, population need or verified capability. "
        "Click a point to open its evidence review."
    )
    try:
        points = event.selection.points  # type: ignore[union-attr]
        if points:
            return points[0]["customdata"][0]
    except (AttributeError, KeyError, IndexError, TypeError):
        pass
    return None


def national_evidence_map(centroids: pd.DataFrame) -> tuple[str, str] | None:
    """District-centroid bubble map of the national evidence landscape (D25).

    One bubble per district: color = existing regional evidence status,
    size = number of supplied records, centroid = median of the district's
    validly-located facility coordinates. Built entirely from the processed
    data - no external boundary geometry. Returns a freshly clicked
    (state, district), applied once so the selection controls stay free.
    """
    if centroids.empty:
        st.info("No districts with usable coordinates - use the selectors instead.")
        return None
    plot = centroids.copy()
    plot["status_label"] = plot["region_status"].map(
        lambda s: f"{CLASS_ICONS.get(s, '⚪')} {s}"
    )
    order = [f"{CLASS_ICONS[s]} {s}" for s in REGION_STATUS_ORDER]
    colors = {f"{CLASS_ICONS[s]} {s}": REGION_COLORS[s] for s in REGION_STATUS_ORDER}
    fig = px.scatter_map(
        plot,
        lat="lat",
        lon="lon",
        color="status_label",
        size="facility_count",
        size_max=26,
        category_orders={"status_label": order},
        color_discrete_map=colors,
        custom_data=[
            "state",
            "district",
            "facility_count",
            "trusted_icu_count",
            "needs_review_count",
            "likely_gap_count",
            "insufficient_data_count",
            "pct_sufficient_data",
        ],
        zoom=3.4,
        center={"lat": 22.5, "lon": 80.0},
        height=520,
    )
    template = (
        "<b>%{customdata[1]}, %{customdata[0]}</b><br>STATUS<br>"
        "%{customdata[2]} facility records<br>"
        "🟢 %{customdata[3]} trusted · 🟡 %{customdata[4]} need review<br>"
        "🔴 %{customdata[5]} no ICU evidence · ⚪ %{customdata[6]} insufficient<br>"
        "%{customdata[7]}% judgeable<br><i>Click to investigate</i><extra></extra>"
    )
    for trace in fig.data:
        trace.hovertemplate = template.replace("STATUS", str(trace.name))
    fig.update_layout(
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.0, "font": {"size": 12}},
        margin={"l": 0, "r": 0, "t": 10, "b": 0},
        map_style="open-street-map",
    )
    event = st.plotly_chart(
        fig, width="stretch", on_select="rerun", selection_mode="points", key="national_map"
    )
    try:
        points = event.selection.points  # type: ignore[union-attr]
    except (AttributeError, TypeError):
        points = []
    if not points:
        # Selection cleared (or fresh mount): allow the next click to apply.
        st.session_state.pop("last_map_region_applied", None)
        return None
    clicked = (str(points[0]["customdata"][0]), str(points[0]["customdata"][1]))
    if clicked == st.session_state.get("last_map_region_applied"):
        return None
    st.session_state["last_map_region_applied"] = clicked
    return clicked


def national_landing(subset: pd.DataFrame, summary: dict, centroids: pd.DataFrame, n_districts: int) -> None:
    """All-India entry view: evidence landscape map + India-wide panel."""
    left, right = st.columns([3, 2], gap="medium")
    with left:
        st.subheader("India ICU evidence landscape", anchor=False)
        st.caption(
            "Explore where supplied records contain trusted ICU evidence, where claims "
            "require verification, and where the data is too incomplete to assess. "
            "Bubble size = number of supplied records."
        )
        clicked = national_evidence_map(centroids)
        missing = max(0, n_districts - len(centroids))
        st.caption(
            f"⚠️ {LANDSCAPE_CAPTION}"
            + (
                f" {missing} district(s) without validly-located records are not on this "
                "map - use the selectors above."
                if missing
                else ""
            )
        )
        if clicked:
            st.session_state["pending_scenario"] = {"state": clicked[0], "district": clicked[1]}
            st.rerun()
    with right:
        st.subheader("India-wide evidence", anchor=False)
        guidance = regional_guidance(summary["region_status"])
        m1, m2 = st.columns(2)
        m1.metric("Facility records", summary["facility_count"])
        m2.metric(
            "Judgeable records",
            f"{summary['pct_sufficient_data']:.0f} %",
            help=(
                "Share of records whose fields are populated enough to evaluate what "
                "the record claims (record judgeability). Populated fields are not "
                "necessarily ICU-informative, and this is NOT operational readiness."
            ),
        )
        m3, m4 = st.columns(2)
        m3.metric(
            f"{CLASS_ICONS[CLASS_TRUSTED]} Trusted ICU evidence",
            summary["trusted_icu_count"],
            help="Records meeting the Trusted ICU evidence standard under the current rules.",
        )
        m4.metric(
            f"{CLASS_ICONS[CLASS_NEEDS_REVIEW]} Needs verification",
            summary["needs_review_count"],
            help="Records with unresolved ICU claims - the human-review worklist.",
        )
        distribution_bar(subset)
        st.markdown(f"**{guidance.meaning}**")
        st.markdown(f"**Recommended next action** — {guidance.action}")
        st.info(
            "Select a district — or click a bubble on the map — to investigate the evidence."
        )
        st.caption(f"⚠️ {REGION_DISCLAIMER}")


def hero_card(region_label: str, summary: dict) -> None:
    """The regional assessment card: status, meaning, action, counts."""
    guidance = regional_guidance(summary["region_status"])
    with st.container(border=True):
        st.header(region_label, anchor=False)
        st.markdown(status_chip(summary["region_status"]), unsafe_allow_html=True)
        st.markdown(f"**{guidance.meaning}**")
        st.markdown(f"**Recommended next action** — {guidance.action}")
        st.markdown(
            f'<div class="cg-counts">{hero_counts_html(summary)}</div>',
            unsafe_allow_html=True,
        )
        if summary["region_status"] == REGION_DATA_DESERT and summary["facility_count"] > 0:
            st.caption(
                "⚠️ This region is a **data desert**: the records are too thin to judge. "
                "Treat it as *unknown*, never as an established ICU gap."
            )
        st.caption(f"⚠️ {REGION_DISCLAIMER}")


def decision_path_row(summary: dict, config) -> None:
    """'Why this status?' - the regional logic as connected step cards."""
    st.markdown("**Why this status?**")
    steps = decision_path(summary, config)
    cols = st.columns(len(steps))
    for col, step in zip(cols, steps, strict=True):
        with col, st.container(border=True):
            st.caption(step.question)
            st.markdown(f"{step.icon} **{step.outcome}**", help=step.detail)


def metrics_row(summary: dict) -> None:
    """Planner-first metric cards; technical metrics live in the expander."""
    cols = st.columns(4)
    cols[0].metric("Facility records", summary["facility_count"])
    cols[1].metric(
        "Judgeable records",
        f"{summary['pct_sufficient_data']:.0f} %",
        help=(
            "Share of records whose fields are populated enough to evaluate what "
            "the record claims (record judgeability). Populated fields are not "
            "necessarily ICU-informative, and this is NOT operational readiness."
        ),
    )
    cols[2].metric(
        f"{CLASS_ICONS[CLASS_TRUSTED]} Trusted ICU evidence",
        summary["trusted_icu_count"],
        help="Records meeting the Trusted ICU evidence standard under the current rules.",
    )
    cols[3].metric(
        f"{CLASS_ICONS[CLASS_NEEDS_REVIEW]} Needs verification",
        summary["needs_review_count"],
        help="Records with unresolved ICU claims - the human-review worklist.",
    )


def technical_metrics_expander(summary: dict, mix_sentence: str | None = None) -> None:
    with st.expander("How this regional assessment was calculated"):
        if mix_sentence:
            st.markdown(f"**Facility mix (name-based context, display only):** {mix_sentence}")
            st.caption(CONTEXT_CAPTION)
        cols = st.columns(2)
        cols[0].metric(
            "Trust-weighted ICU evidence index",
            f"{summary['trust_weighted_icu_coverage']:.2f}",
            help=(
                "0-1 average capability-evidence score weighted by record "
                "completeness. This is not population or geographic coverage."
            ),
        )
        cols[1].metric(
            "Trusted-record share",
            f"{summary['evidence_coverage_pct']:.0f} %",
            help=(
                "Share of supplied facility records classified as Trusted under the "
                "current evidence rules - not the share of facilities with an ICU."
            ),
        )
        st.markdown(f"**Stored regional reasoning:** {summary['region_status_reason']}")
        st.caption(
            f"Thresholds and the complete Trusted rule are documented in the sidebar "
            f"under “About this assessment → {EVIDENCE_POLICY_TITLE}”. "
            f"{EVIDENCE_POLICY_CAPTION}"
        )


def regions_requiring_attention(region_district: pd.DataFrame, state: str) -> None:
    """Districts grouped by EXISTING regional status - no new ranking.

    Every district line is clickable and drives the normal selection (and
    thereby the URL parameters and workflow state), just like the map
    bubbles and the example shortcuts.
    """
    df = region_district
    if state != "All India":
        df = region_district[region_district["state"] == state]
    st.markdown("**Regions requiring attention** (grouped by existing regional status)")
    groups = [
        ("🔴", "Potential planning gap", REGION_PLANNING_GAP),
        ("🟡", "Needs facility verification", REGION_NEEDS_REVIEW),
        ("⚪", "Insufficient data", REGION_DATA_DESERT),
    ]
    cols = st.columns(3)
    for col, (icon, label, status) in zip(cols, groups, strict=True):
        rows = df[df["region_status"] == status].sort_values("facility_count", ascending=False)
        with col, st.container(border=True):
            st.markdown(f"{icon} **{label}** — {len(rows)} district(s)")
            for _, r in rows.head(5).iterrows():
                if st.button(
                    f"{r['state']} / {r['district']} ({int(r['facility_count'])} records)",
                    key=f"attn_{status}_{r['state']}_{r['district']}",
                    type="tertiary",
                    help="Open this district's evidence view",
                ):
                    st.session_state["pending_scenario"] = {
                        "state": str(r["state"]),
                        "district": str(r["district"]),
                    }
                    request_scroll("understand-evidence")
                    st.rerun()
            if len(rows) > 5:
                st.caption(f"… and {len(rows) - 5} more")


def priority_facilities_section(subset: pd.DataFrame, region_status: str) -> None:
    """Up to five facilities the planner should open first."""
    priority = select_priority_facilities(subset, region_status)
    if priority.empty:
        st.caption("No facilities require prioritized attention in this selection.")
        return
    st.markdown("**Facilities requiring attention**")
    st.caption(CONTEXT_CAPTION)
    for _, row in priority.iterrows():
        with st.container(border=True):
            info, action = st.columns([5, 1])
            with info:
                flag = primary_flag(row)
                place = " · ".join(
                    str(v)
                    for v in (row.get("address_city"), row.get("district_final"))
                    if pd.notna(v) and str(v).strip()
                )
                st.markdown(
                    f"**{row['name']}** &nbsp; {CLASS_ICONS[row['classification']]} "
                    f"{facility_display_label(row['classification'])}  \n"
                    f"{place or '-'} · evidence {row['capability_evidence_score']} · "
                    f"judgeability {row['data_completeness_score']}"
                    + (f" · {int(row['n_validation_flags'])} flag(s)" if row["n_validation_flags"] else "")
                )
                st.caption(
                    f"{row['facility_context']} · {row['priority_reason']}"
                    + (f" — {flag}" if flag else "")
                )
            with action:
                if st.button("Review evidence", key=f"review_{row['unique_id']}"):
                    st.session_state["focus_facility"] = row["unique_id"]
                    st.session_state["wf_facility_reviewed"] = True
                    request_scroll("facility-review")
                    st.rerun()


def scenarios_panel(
    state: str,
    district: str | None,
    summary: dict,
    subset: pd.DataFrame,
    scored: pd.DataFrame,
    config,
) -> None:
    """Step 4: save the current planning view; list/reopen/delete scenarios."""
    store = scenario_store()
    st.caption(
        "Save the selected region, its evidence status, metrics and your notes so the "
        "decision can be reopened and reviewed later - by you or a colleague. A scenario "
        "records supplied-record evidence, not a verified coverage assessment."
    )
    snapshot = data_snapshot_id(scored)
    attachable = len(subset) <= 50

    if saved_msg := st.session_state.pop("scenario_saved_msg", None):
        st.success(saved_msg)
    with st.container(border=True), st.form(key="scenario_form", clear_on_submit=True):
        st.markdown("**💾 Save this planning scenario**")
        name = st.text_input("Scenario name", max_chars=120)
        cols = st.columns(2)
        with cols[0]:
            author = st.text_input("Author (optional)", max_chars=80, key="scenario_author")
        with cols[1]:
            include_ids = st.checkbox(
                f"Attach the {len(subset)} facility ID(s) in this selection"
                if attachable
                else f"Attach facility IDs (disabled: {len(subset)} records in selection, max 50)",
                value=False,
                disabled=not attachable,
            )
        note = st.text_area(
            "Planner note (optional)",
            placeholder="e.g. Verify the two flagged facilities before budgeting.",
        )
        if st.form_submit_button("Save scenario", type="primary"):
            if not name.strip():
                st.error("Give the scenario a name.")
            else:
                scenario = scenario_from_summary(
                    name=name,
                    summary=summary,
                    state=None if state == "All India" else state,
                    district=district,
                    author=author,
                    note=note.strip(),
                    selected_facility_ids=(
                        subset["unique_id"].tolist() if include_ids and attachable else []
                    ),
                    scoring_config_hash=scoring_config_fingerprint(config),
                    data_snapshot=snapshot,
                )
                with st.spinner("Saving scenario…"):
                    saved = store.save_scenario(scenario)
                # Marking Step 4 complete needs a rerun so the sidebar and the
                # main indicator update together; the message survives it.
                st.session_state["wf_scenario_saved"] = True
                st.session_state["scenario_saved_msg"] = (
                    f"Saved scenario “{saved.name}” ({saved.region_label})."
                )
                st.rerun()

    with st.spinner("Loading scenarios…"):
        saved_scenarios = store.list_scenarios()
    with st.expander(f"📂 Saved scenarios ({len(saved_scenarios)})"):
        if not saved_scenarios:
            st.caption("No saved scenarios yet.")
            return
        options = {
            f"{s.name} — {s.region_label} ({s.created_at})": s.id for s in saved_scenarios
        }
        choice = st.selectbox("Scenario", list(options.keys()))
        scenario = store.get_scenario(options[choice])
        if scenario is None:
            st.warning("Scenario no longer exists.")
            return
        st.markdown(
            f"**{scenario.name}** — {scenario.region_label} · {scenario.capability}  \n"
            f"Saved {scenario.created_at} by {scenario.author or 'anonymous'}  \n"
            f"Status then: {scenario.region_status}  \n"
            f"Records {scenario.facility_count} · trusted {scenario.trusted_count} · "
            f"review {scenario.needs_review_count} · no-ICU-evidence "
            f"{scenario.no_icu_evidence_count} · insufficient {scenario.insufficient_data_count}  \n"
            f"Judgeable {scenario.judgeable_pct:.0f} % · trusted-record share "
            f"{scenario.trusted_record_share_pct:.0f} % · evidence index "
            f"{scenario.trust_weighted_evidence_index:.2f}"
        )
        st.caption(
            f"Audit trail: evidence policy `{scenario.scoring_config_hash}` · "
            f"data snapshot `{scenario.data_snapshot}`"
        )
        if scenario.note:
            st.markdown(f"> {scenario.note}")
        if scenario.selected_facility_ids:
            st.caption(f"{len(scenario.selected_facility_ids)} attached facility ID(s).")
        if scenario.data_snapshot != snapshot:
            st.warning(
                "This scenario was saved against an older version of the data - its "
                "numbers may differ from what the app currently shows."
            )
        col_open, col_delete = st.columns(2)
        with col_open:
            if st.button("Reopen scenario", key=f"reopen_{scenario.id}"):
                st.session_state["pending_scenario"] = {
                    "state": scenario.state,
                    "district": scenario.district,
                }
                st.rerun()
        with col_delete:
            confirmed = st.checkbox("Confirm deletion", key=f"confirm_delete_{scenario.id}")
            if st.button("Delete scenario", key=f"delete_{scenario.id}", disabled=not confirmed):
                store.delete_scenario(scenario.id)
                st.rerun()


def notes_panel(scope_type: str, scope_id: str, context: str) -> None:
    """Reviewer note form + history for the given scope."""
    store = note_store()
    st.markdown(f"**Reviewer notes** - {scope_type}: `{scope_id}`")
    # The Delta-backed store answers via a SQL warehouse, which can take a
    # few seconds when cold - show a real loading state instead of letting
    # a stale "No notes yet." linger while the query runs.
    with st.spinner("Loading notes…"):
        existing = store.list_notes(scope_type=scope_type, scope_id=scope_id)
    for n in existing:
        st.markdown(f"> {n.note}\n>\n> - *{n.author or 'anonymous'}, {n.created_at}*")
    if not existing:
        st.caption("No notes yet.")
    with st.form(key=f"note_form_{scope_type}_{scope_id}", clear_on_submit=True):
        note_text = st.text_area(
            "New note",
            placeholder="e.g. Verify these facilities before classifying this district as an ICU desert.",
        )
        author = st.text_input("Author (optional)", max_chars=80)
        if st.form_submit_button("Save note") and note_text.strip():
            with st.spinner("Saving note…"):
                store.add_note(
                    ReviewNote(scope_type=scope_type, scope_id=scope_id, note=note_text, author=author)
                )
            st.rerun()
    st.caption(context)


# Display order for evidence fragments: contradictions first (they block
# trust), then the claim itself, then its corroboration.
_FRAGMENT_GROUP_ORDER = [
    "negation",
    "explicit_icu",
    "equipment",
    "procedure",
    "staffing",
    "specialty_context",
]


def _fragment_sort_key(fragment: dict) -> tuple[int, str]:
    group = fragment.get("group", "")
    try:
        return (_FRAGMENT_GROUP_ORDER.index(group), group)
    except ValueError:
        return (len(_FRAGMENT_GROUP_ORDER), group)


def facility_detail(row: pd.Series) -> None:
    """Planner-first drilldown: decision summary -> exact evidence ->
    missing/uncertain -> reviewer note -> collapsed technical details.
    Nothing is removed; only the default hierarchy changed (D24)."""
    classification = row["classification"]
    reason = row["classification_reason"]
    if classification == CLASS_LIKELY_GAP:
        # Display-level wording: processed data built before D19 stores the
        # old "likely a real capability gap" sentence.
        reason = (
            f"This judgeable record (completeness {row['data_completeness_score']}) contains "
            "no credible ICU evidence. The regional layer decides whether that pattern "
            "becomes a potential planning gap."
        )
    subtypes = json.loads(row.get("icu_subtypes_json") or "[]")
    pretty_subtypes = [SUBTYPE_LABELS.get(s, s) for s in subtypes]
    specialised_only = bool(subtypes) and SUBTYPE_GENERAL not in subtypes
    operational = assess_operational_data(row)
    status = assessment_status(classification)
    fragments = sorted(json.loads(row["evidence_fragments_json"]), key=_fragment_sort_key)
    all_flags = json.loads(row["validation_flags_json"])
    unresolved_flags = [f for f in all_flags if f["severity"] in ("contradiction", "suspicious")]

    # ---------------- A. Decision summary ----------------
    with st.container(border=True):
        st.subheader(row["name"] if pd.notna(row["name"]) else "(unnamed facility)", anchor=False)
        st.markdown(status_chip(classification), unsafe_allow_html=True)
        st.markdown(f"{status.icon} **{status.headline}**", help=status.help_text)
        st.markdown(f"**Reason:** {reason}")
        if subtypes:
            if specialised_only:
                st.warning(
                    f"⚕️ **ICU evidence type: {', '.join(pretty_subtypes)} only.** Specialised "
                    "intensive-care evidence does not automatically establish general "
                    "adult ICU capability."
                )
            else:
                st.markdown(f"⚕️ **ICU evidence type:** {', '.join(pretty_subtypes)}")
        c1, c2, c3 = st.columns(3)
        c1.metric(
            "ICU evidence strength",
            f"{row['capability_evidence_score']} / 100",
            help="How strongly the supplied record supports ICU capability.",
        )
        c2.metric(
            "Record judgeability",
            f"{row['data_completeness_score']} / 100",
            help=(
                "Whether the record's fields are populated enough to evaluate what it "
                "claims. Not 'fully documented', not ICU-informative content, not "
                "operational data availability."
            ),
        )
        c3.metric(
            "Operational data",
            f"{operational.available} of {operational.total}",
            operational.level,
            delta_color="off",
            help=OPERATIONAL_HELP,
        )
        st.markdown(f"**Recommended reviewer action:** {reviewer_action(classification)}")

    # ---------------- B. Exact evidence ----------------
    st.markdown("##### Exact evidence (verbatim from the supplied record)")
    if fragments:
        for fragment in fragments[:5]:
            icon = "🔴" if fragment["group"] == "negation" else "🔎"
            st.markdown(f"{icon} *{fragment['group']}* — from `{fragment['field']}`:")
            st.code(fragment["text"], language=None)
        if len(fragments) > 5:
            with st.expander(f"View all evidence fragments ({len(fragments)})"):
                for fragment in fragments[5:]:
                    icon = "🔴" if fragment["group"] == "negation" else "🔎"
                    st.markdown(f"{icon} *{fragment['group']}* — from `{fragment['field']}`:")
                    st.code(fragment["text"], language=None)
    else:
        st.caption(
            "No ICU-related text found in this record. This means the record shows no "
            "evidence - not that the facility has been verified to lack an ICU."
        )

    # ---------------- C. Missing or uncertain ----------------
    st.markdown("##### Missing or uncertain")
    if specialised_only:
        st.markdown(
            "- ⚕️ General adult ICU evidence is **absent** — only specialised subtype "
            "evidence is present."
        )
    for f in unresolved_flags:
        icon = "🔴" if f["severity"] == "contradiction" else "🟡"
        st.markdown(f"- {icon} `{f['name']}` — {f['detail']}")
    if operational.source_warning:
        st.markdown(f"- ⚠️ {operational.source_warning}")
    missing = json.loads(row["missing_evidence_json"])
    for m in missing:
        st.markdown(f"- {m}")
    unavailable = [
        OPERATIONAL_COMPONENTS[key] for key, present in operational.components.items() if not present
    ]
    if unavailable:
        st.markdown(f"- Operational fields not stated: {', '.join(unavailable)}")
    if not (specialised_only or unresolved_flags or missing or unavailable):
        st.caption("Nothing missing or uncertain for this record.")

    # ---------------- D. Reviewer action ----------------
    notes_panel(
        "facility",
        row["unique_id"],
        "Notes are saved with this facility and visible to anyone who reviews it later.",
    )

    # ---------------- E. Technical details (collapsed) ----------------
    with st.expander("View full supplied record"):
        st.caption(
            "Fields below are structured claims generated upstream from website text "
            "and images by the dataset's extraction pipeline - not verified hospital "
            "statements."
        )
        place = ", ".join(
            str(v)
            for v in [row.get("address_line1"), row.get("address_city"), row.get("state_final")]
            if pd.notna(v) and str(v).strip()
        )
        st.markdown(f"**Address:** {place or '-'}  \n**PIN:** {row.get('pincode_clean') or '-'}")
        st.markdown(f"**District (from PIN):** {row.get('district_final') or '-'}")
        st.markdown(
            f"**Capacity:** {row.get('capacity') or '-'} | **Doctors:** {row.get('numberDoctors') or '-'}"
        )
        if pd.notna(row.get("description")):
            st.markdown(f"**Description:** {row['description']}")
        for field in ("capability", "specialties", "procedure", "equipment"):
            items = parse_list_field(row.get(field))
            if items:
                st.markdown(f"**{field} ({len(items)} entries):**")
                for item in items:
                    st.markdown(f"- {item}")

    with st.expander("View scoring and validator details"):
        corroboration = json.loads(row.get("corroboration_categories_json") or "[]")
        min_corr = load_scoring_config().thresholds.min_corroboration_categories
        st.markdown(
            f"**Distinct evidence categories (within the supplied record):** "
            f"{', '.join(corroboration) if corroboration else 'none'} "
            f"({len(corroboration)} of {min_corr} required for Trusted)"
        )
        ev_comp = json.loads(row["evidence_components_json"])
        comp_comp = json.loads(row["completeness_components_json"])
        st.markdown("**Score breakdown**")
        breakdown = pd.DataFrame(
            [{"score": "evidence", "component": k, "points": v} for k, v in ev_comp.items()]
            + [{"score": "completeness", "component": k, "points": v} for k, v in comp_comp.items()]
        )
        if not breakdown.empty:
            st.dataframe(breakdown, hide_index=True, width="stretch")
        else:
            st.caption("No score components - nothing in this record carries signal.")
        st.markdown("**All validator flags**")
        if all_flags:
            sev_icon = {"contradiction": "🔴", "suspicious": "🟡", "data_quality": "⚪"}
            for f in all_flags:
                st.markdown(f"{sev_icon.get(f['severity'], '⚪')} `{f['name']}` - {f['detail']}")
        else:
            st.caption("No validator flags.")
        st.markdown(f"**Operational data availability: {operational.summary}**")
        for key, present in operational.components.items():
            st.markdown(f"{'✅' if present else '⬜'} {OPERATIONAL_COMPONENTS[key]}")
        st.markdown("**Operational field details**")
        for key, label in OPERATIONAL_COMPONENTS.items():
            st.markdown(f"- **{label}** — {operational.details.get(key, '-')}")
        st.caption(
            "ICU evidence strength, record judgeability and operational data "
            "availability are separate questions; the checklist is descriptive "
            "only and never changes the classification."
        )

    with st.expander("View source and provenance details"):
        st.markdown(
            f"**Geography source:** `{row.get('geo_source')}`"
            + (" ⚠️ state field disagrees with PIN directory" if row.get("geo_conflict") else "")
        )
        urls = parse_list_field(row.get("source_urls"))
        if urls:
            st.markdown(f"**Source URLs ({len(urls)}):**")
            for u in dict.fromkeys(urls):
                st.markdown(f"- {u}")
        else:
            st.caption("No source URLs in the supplied record.")


def sidebar_nav(wf_state) -> None:
    """The task-oriented planner workflow - the sidebar's primary content."""
    with st.sidebar:
        st.markdown("### CareGap workflow")
        st.markdown(sidebar_workflow_html(wf_state), unsafe_allow_html=True)


def sidebar_secondary(config) -> None:
    """Methodology and limitations: one collapsed expander at the bottom."""
    with st.sidebar, st.expander("About this assessment", expanded=False):
        st.markdown(f"**{EVIDENCE_POLICY_TITLE}**")
        for line in evidence_policy_lines(config):
            st.markdown(line)
        st.caption(EVIDENCE_POLICY_CAPTION)
        st.divider()
        st.markdown("**Methodology**")
        st.markdown(
            "Every classification is traceable to exact text fragments from the "
            "supplied facility record. Two independent scores (ICU evidence "
            "strength, record judgeability) plus deterministic validators feed a "
            "four-state classification; regions separate potential planning gaps "
            "from data deserts. See README.md and DECISIONS.md in the repository."
        )
        st.divider()
        st.markdown("**Dataset limitations**")
        st.markdown(
            "The supplied fields are structured claims generated upstream from "
            "website text and images - not verified clinical facts. CareGap Map "
            "assesses evidence in supplied facility records. It does not verify "
            "current ICU operation, bed availability, staffing availability or "
            "clinical service access."
        )
        st.caption(REGION_DISCLAIMER)
        st.divider()
        st.markdown("**Technical details**")
        st.markdown(
            f"- Scoring-config fingerprint: `{scoring_config_fingerprint(config)}`\n"
            "- Data source: precomputed deterministic snapshot (no live model calls)\n"
            "- State/district selection persists in the URL query parameters"
        )


def main() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)
    # The title is a link back to the fresh All-India view: "./" drops the
    # ?state=&district= parameters, so the national evidence landscape shows.
    st.markdown(
        '<div class="cg-title"><a href="./" target="_self" '
        'title="Back to the India-wide evidence overview" '
        'aria-label="CareGap Map — back to the India-wide overview">'
        "🏥 CareGap Map</a></div>"
        '<div class="cg-subtitle">ICU evidence for public-health planning — separate '
        "potential ICU planning gaps from places where the data is simply insufficient."
        "</div>",
        unsafe_allow_html=True,
    )

    try:
        scored, region_state, region_district = load_data()
    except MissingDataError as exc:
        st.error(
            f"{exc}\n\nRun the pipeline first:\n\n"
            "```\npython scripts/profile_data.py\npython scripts/build_processed_data.py\n```"
        )
        st.stop()
        return

    config = load_scoring_config()

    # ---------------- 1 · Select region (top control bar) ----------------
    anchor("select-region")
    # A reopened scenario sets the selection before the widgets instantiate;
    # otherwise a fresh session (e.g. after a page refresh) restores the
    # region from the URL query parameters. Unknown values fall back to
    # All India because they simply never match the widget options.
    pending = st.session_state.pop("pending_scenario", None)
    if pending is None and "state_select" not in st.session_state:
        pending = normalize_region_request(
            st.query_params.get("state"), st.query_params.get("district")
        )

    ctrl = st.columns([1.1, 1, 1])
    with ctrl[0]:
        st.selectbox(
            "Capability",
            ["ICU — prototype scope"],
            disabled=True,
            help=(
                "CareGap Map currently supports ICU evidence assessment. Other "
                "healthcare capabilities require separately calibrated evidence "
                "vocabularies, validators, thresholds and human evaluation. The "
                "architecture is capability-extensible, but this prototype "
                "deliberately validates ICU deeply rather than applying shallow "
                "keyword rules across multiple clinical capabilities."
            ),
        )
    states = sorted(s for s in scored["state_final"].dropna().unique())
    state_options = ["All India"] + states + [UNASSIGNED]
    if pending is not None:
        target_state = pending.get("state") or "All India"
        if target_state in state_options:
            st.session_state["state_select"] = target_state
    with ctrl[1]:
        state = st.selectbox("State", state_options, key="state_select")
    district = None
    with ctrl[2]:
        if state not in ("All India", UNASSIGNED):
            districts = sorted(
                d
                for d in scored.loc[scored["state_final"] == state, "district_final"]
                .fillna(UNASSIGNED)
                .unique()
            )
            district_options = ["All districts"] + districts
            if pending is not None:
                target_district = pending.get("district") or "All districts"
                if target_district in district_options:
                    st.session_state["district_select"] = target_district
            district = st.selectbox("District (optional)", district_options, key="district_select")
            if district == "All districts":
                district = None
        else:
            st.selectbox(
                "District (optional)",
                ["All districts"],
                disabled=True,
                key="district_placeholder",
                help="Pick a state first to narrow down to a district.",
            )

    # Reflect the selection in the URL so a page refresh keeps the region.
    # Only public region names are stored - never notes or identifiers.
    desired_params = desired_region_params(state, district)
    for key in ("state", "district"):
        if key in st.query_params and key not in desired_params:
            del st.query_params[key]
        elif desired_params.get(key) and st.query_params.get(key) != desired_params[key]:
            st.query_params[key] = desired_params[key]

    # Optional deterministic demo shortcuts (from the CURRENT data only).
    examples = example_regions(region_district, scored)
    if examples:
        with st.expander("✨ Explore an example"):
            cols = st.columns(len(examples))
            for col, (label, (ex_state, ex_district)) in zip(
                cols, examples.items(), strict=True
            ):
                with col:
                    if st.button(f"{label}: {ex_state} / {ex_district}", key=f"example_{label}"):
                        st.session_state["pending_scenario"] = {
                            "state": ex_state,
                            "district": ex_district,
                        }
                        request_scroll("understand-evidence")
                        st.rerun()

    # ---------------- Filter the facility set ----------------
    subset = scored
    if state == UNASSIGNED:
        subset = scored[scored["state_final"].isna()]
    elif state != "All India":
        subset = scored[scored["state_final"] == state]
        if district == UNASSIGNED:
            subset = subset[subset["district_final"].isna()]
        elif district:
            subset = subset[subset["district_final"] == district]

    region_label = state if state != "All India" else "India"
    if district:
        region_label = f"{state} / {district}"

    # ---------------- Workflow state (session facts only, D26) ----------------
    region_key = f"{state}|{district or ''}"
    if st.session_state.get("wf_region") != region_key:
        # A new investigation resets the downstream workflow flags.
        st.session_state["wf_region"] = region_key
        st.session_state.pop("wf_facility_reviewed", None)
        st.session_state.pop("wf_scenario_saved", None)
    wf_state = infer_workflow_state(
        region_selected=state != "All India",
        facility_reviewed=bool(st.session_state.get("wf_facility_reviewed")),
        scenario_saved=bool(st.session_state.get("wf_scenario_saved")),
    )
    # Compact indicator: the sidebar collapses on narrow screens, so the
    # current step stays visible in the main content too.
    st.caption(current_step_label(wf_state))

    # ---------------- 2 · Understand the evidence ----------------
    anchor("understand-evidence")
    summary = summarize_facilities(subset, config)
    if state == "All India":
        # National entry view: the evidence landscape is the visual front door.
        centroids = district_centroids(scored, region_district)
        n_districts = len(
            region_district[
                (region_district["state"] != UNASSIGNED)
                & (region_district["district"] != UNASSIGNED)
            ]
        )
        national_landing(subset, summary, centroids, n_districts)
        decision_path_row(summary, config)
        technical_metrics_expander(summary)
        st.caption(
            "National numbers are high-level summaries — select a district for the "
            "planning view."
        )
    else:
        hero_card(region_label, summary)
        # District-level facility mix (display-only context, D27): a
        # planning-gap result carried mostly by clinics/labs deserves a
        # visible caution before anyone interprets it.
        mix_sentence = None
        if district:
            mix = facility_mix_counts(subset)
            mix_sentence = facility_mix_sentence(mix, len(subset))
            if summary["region_status"] == REGION_PLANNING_GAP and mix_warning_applies(mix):
                st.warning(f"⚠️ {MIX_GAP_WARNING}")
        decision_path_row(summary, config)
        metrics_row(summary)
        distribution_bar(subset)
        technical_metrics_expander(summary, mix_sentence)
        if not district:
            st.caption(
                "State-level numbers are high-level summaries — select a district for the "
                "planning view."
            )

    # Context-aware regional visualization.
    map_selected_id: str | None = None
    if district:
        st.markdown("**Facility evidence map**")
        map_selected_id = facility_map(subset)
    else:
        regions_requiring_attention(region_district, state)
        if state == "All India":
            classification_chart(
                scored.assign(region=scored["state_final"].fillna(UNASSIGNED)),
                "region",
                "Facility evidence status by state",
            )
        elif len(subset):
            classification_chart(
                subset.assign(region=subset["district_final"].fillna(UNASSIGNED)),
                "region",
                f"Facility evidence status by district - {state}",
            )
        with st.expander("🗺️ Facility evidence map"):
            map_selected_id = facility_map(subset)

    # A facility clicked on the district map counts as an explicit review
    # action; the guarded rerun keeps the indicator and sidebar in sync.
    if map_selected_id and not st.session_state.get("wf_facility_reviewed"):
        st.session_state["wf_facility_reviewed"] = True
        request_scroll("facility-review")
        st.rerun()

    # ---------------- 3 · Review priority facilities ----------------
    anchor("review-facilities")
    st.header("3 · Review priority facilities", anchor=False)
    priority_facilities_section(subset, summary["region_status"])

    table = subset.sort_values(
        ["capability_evidence_score", "data_completeness_score"], ascending=False
    )
    with st.expander(f"View all {len(subset)} facility records"):
        class_filter = st.multiselect(
            "Filter by evidence status",
            CLASS_STACK_ORDER,
            default=CLASS_STACK_ORDER,
            format_func=lambda c: f"{CLASS_ICONS[c]} {facility_display_label(c)}",
        )
        filtered = table[table["classification"].isin(class_filter)]
        st.dataframe(
            filtered[
                [
                    "name",
                    "address_city",
                    "district_final",
                    "state_final",
                    "classification",
                    "capability_evidence_score",
                    "data_completeness_score",
                    "n_validation_flags",
                ]
            ]
            .assign(classification=filtered["classification"].map(facility_display_label))
            .rename(
                columns={
                    "address_city": "city",
                    "district_final": "district",
                    "state_final": "state",
                    "classification": "evidence status",
                    "capability_evidence_score": "evidence 0-100",
                    "data_completeness_score": "completeness 0-100",
                    "n_validation_flags": "flags",
                }
            ),
            hide_index=True,
            width="stretch",
            height=320,
        )

    # ---------------- Facility evidence review (drilldown) ----------------
    if table.empty:
        st.info("No facilities match the current selection.")
    else:
        options = {
            f"{r['name']} - {r.get('address_city') or '?'} ({r['unique_id'][:8]})": idx
            for idx, r in table.iterrows()
        }
        option_labels = list(options.keys())
        ids = table["unique_id"].tolist()
        # A "Review evidence" click or a map click focuses the drilldown ONCE,
        # then leaves the dropdown free for manual navigation.
        focus_id = st.session_state.pop("focus_facility", None) or map_selected_id
        if focus_id in ids and focus_id != st.session_state.get("last_focus_applied"):
            st.session_state["facility_select"] = option_labels[ids.index(focus_id)]
            st.session_state["last_focus_applied"] = focus_id
        if st.session_state.get("facility_select") not in option_labels:
            st.session_state.pop("facility_select", None)
        anchor("facility-review")
        choice = st.selectbox(
            "Inspect a facility",
            option_labels,
            key="facility_select",
            on_change=lambda: st.session_state.__setitem__("wf_facility_reviewed", True),
        )
        facility_detail(table.loc[options[choice]])

    # ---------------- Region-level notes ----------------
    if state != "All India":
        scope_exists = (district and district != UNASSIGNED) or state != UNASSIGNED
        if scope_exists:
            with st.expander("🗒️ Region reviewer notes"):
                if district and district != UNASSIGNED:
                    notes_panel(
                        "district", f"{state}/{district}", "Attached to the selected district."
                    )
                else:
                    notes_panel("state", state, "Attached to the selected state.")

    # ---------------- 4 · Save a planning scenario ----------------
    anchor("save-scenario")
    st.header("4 · Save a planning scenario", anchor=False)
    try:
        scenarios_panel(state, district, summary, subset, scored, config)
    except Exception as exc:  # scenario persistence must never block the demo
        st.warning(f"Planning scenarios are unavailable right now ({exc}). Notes still work.")

    # Sidebar: the task-oriented workflow on top, methodology below.
    sidebar_nav(wf_state)
    sidebar_secondary(config)

    # One-shot scroll requested by a click handler (e.g. Review evidence).
    flush_scroll()


main()
