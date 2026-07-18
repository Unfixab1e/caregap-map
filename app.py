"""CareGap Map - trust layer for ICU coverage planning in India.

Streamlit app: regional trust-weighted ICU coverage with facility-level
evidence drilldown. Run `python scripts/build_processed_data.py` first,
then `streamlit run app.py`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

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
    load_env_file,
    load_scoring_config,
)

load_env_file()  # .env overrides nothing that is already in the environment
from caregap_map.data_access import MissingDataError, get_data_source  # noqa: E402
from caregap_map.persistence import ReviewNote, ReviewStore, get_review_store  # noqa: E402

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

st.set_page_config(page_title="CareGap Map", page_icon="🏥", layout="wide")


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


def status_banner(status: str, reason: str) -> None:
    icon = CLASS_ICONS.get(status, "⚪")
    if status in (CLASS_TRUSTED, REGION_TRUSTED):
        st.success(f"{icon} **{status}** - {reason}")
    elif status in (CLASS_LIKELY_GAP, REGION_PLANNING_GAP):
        st.error(f"{icon} **{status}** - {reason}")
    elif status in (CLASS_NEEDS_REVIEW, REGION_NEEDS_REVIEW):
        st.warning(f"{icon} **{status}** - {reason}")
    else:  # insufficient data / data desert
        st.info(f"⚪ **{status}** - {reason}")


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
        fig.add_trace(
            go.Bar(
                y=counts.index,
                x=counts[cls],
                name=cls,
                orientation="h",
                marker={"color": CLASS_COLORS[cls], "line": {"color": surface, "width": 2}},
                hovertemplate=f"%{{y}}<br>{cls}: %{{x}} facilities<extra></extra>",
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

    located["status"] = located["classification"].map(lambda c: f"{CLASS_ICONS.get(c, '')} {c}")
    order = [f"{CLASS_ICONS[c]} {c}" for c in CLASS_STACK_ORDER]
    colors = {f"{CLASS_ICONS[c]} {c}": CLASS_COLORS[c] for c in CLASS_STACK_ORDER}
    fig = px.scatter_map(
        located,
        lat="lat_parsed",
        lon="lon_parsed",
        color="status",
        category_orders={"status": order},
        color_discrete_map=colors,
        hover_name="name",
        hover_data={
            "lat_parsed": False,
            "lon_parsed": False,
            "status": True,
            "capability_evidence_score": True,
            "data_completeness_score": True,
        },
        custom_data=["unique_id"],
        zoom=3.5,
        height=520,
    )
    fig.update_layout(
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.0},
        margin={"l": 0, "r": 0, "t": 30, "b": 0},
        map_style="open-street-map",
    )
    event = st.plotly_chart(
        fig, width="stretch", on_select="rerun", selection_mode="points", key="facility_map"
    )
    st.caption(
        f"{len(located)} located facility records; **{unlocated} without valid coordinates "
        "are NOT on this map** but appear in the table below. Points show record locations "
        "and evidence status only - not travel time, population need or verified capability."
    )
    try:
        points = event.selection.points  # type: ignore[union-attr]
        if points:
            return points[0]["customdata"][0]
    except (AttributeError, KeyError, IndexError, TypeError):
        pass
    return None


def metrics_row(summary: dict) -> None:
    cols = st.columns(5)
    cols[0].metric("Facility records", summary["facility_count"])
    cols[1].metric(f"{CLASS_ICONS[CLASS_TRUSTED]} Trusted ICU", summary["trusted_icu_count"])
    cols[2].metric(f"{CLASS_ICONS[CLASS_NEEDS_REVIEW]} Needs review", summary["needs_review_count"])
    cols[3].metric(f"{CLASS_ICONS[CLASS_LIKELY_GAP]} Likely gap", summary["likely_gap_count"])
    cols[4].metric(f"{CLASS_ICONS[CLASS_INSUFFICIENT]} Insufficient data", summary["insufficient_data_count"])
    cols = st.columns(3)
    cols[0].metric(
        "Judgeable records",
        f"{summary['pct_sufficient_data']:.0f} %",
        help="Share of records with enough data to be judged at all (data coverage).",
    )
    cols[1].metric(
        "Trust-weighted ICU coverage",
        f"{summary['trust_weighted_icu_coverage']:.2f}",
        help=(
            "0-1: mean capability evidence weighted by data completeness. "
            "Poorly documented claims move this needle less."
        ),
    )
    cols[2].metric(
        "Evidence coverage",
        f"{summary['evidence_coverage_pct']:.0f} %",
        help="Share of records classified as Trusted ICU Coverage.",
    )


def notes_panel(scope_type: str, scope_id: str, context: str) -> None:
    """Reviewer note form + history for the given scope."""
    store = note_store()
    st.markdown(f"**Reviewer notes** - {scope_type}: `{scope_id}`")
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
            store.add_note(
                ReviewNote(scope_type=scope_type, scope_id=scope_id, note=note_text, author=author)
            )
            st.rerun()
    st.caption(context)


def facility_detail(row: pd.Series) -> None:
    """Supplied record, exact evidence fragments, scores and flags."""
    st.subheader(row["name"] if pd.notna(row["name"]) else "(unnamed facility)")
    status_banner(row["classification"], row["classification_reason"])

    subtypes = json.loads(row.get("icu_subtypes_json") or "[]")
    if subtypes:
        pretty = [SUBTYPE_LABELS.get(s, s) for s in subtypes]
        if SUBTYPE_GENERAL not in subtypes:
            st.warning(
                f"⚕️ Intensive-care evidence found: **{', '.join(pretty)} only** — "
                "no general adult ICU claim in this record."
            )
        else:
            st.markdown(f"⚕️ **Intensive-care evidence:** {', '.join(pretty)}")

    left, right = st.columns([1, 1])

    with left:
        st.markdown("#### Supplied record")
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
        st.markdown(
            f"**Geography source:** `{row.get('geo_source')}`"
            + (" ⚠️ state field disagrees with PIN directory" if row.get("geo_conflict") else "")
        )
        st.markdown(f"**District (from PIN):** {row.get('district_final') or '-'}")
        st.markdown(
            f"**Capacity:** {row.get('capacity') or '-'} | **Doctors:** {row.get('numberDoctors') or '-'}"
        )
        if pd.notna(row.get("description")):
            st.markdown(f"**Description:** {row['description']}")
        for field in ("capability", "specialties", "procedure", "equipment"):
            items = parse_list_field(row.get(field))
            if items:
                with st.expander(f"{field} ({len(items)} entries)"):
                    for item in items:
                        st.markdown(f"- {item}")
        urls = parse_list_field(row.get("source_urls"))
        if urls:
            with st.expander(f"source URLs ({len(urls)})"):
                for u in dict.fromkeys(urls):
                    st.markdown(f"- {u}")

    with right:
        st.markdown("#### Trust assessment")
        c1, c2 = st.columns(2)
        c1.metric("Capability evidence", f"{row['capability_evidence_score']} / 100")
        c2.metric("Data completeness", f"{row['data_completeness_score']} / 100")

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

        flags = json.loads(row["validation_flags_json"])
        st.markdown("**Validator flags**")
        if flags:
            sev_icon = {"contradiction": "🔴", "suspicious": "🟡", "data_quality": "⚪"}
            for f in flags:
                st.markdown(f"{sev_icon.get(f['severity'], '⚪')} `{f['name']}` - {f['detail']}")
        else:
            st.caption("No validator flags.")

        missing = json.loads(row["missing_evidence_json"])
        st.markdown("**Missing evidence**")
        if missing:
            for m in missing:
                st.markdown(f"- {m}")
        else:
            st.caption("Nothing missing.")

    st.markdown("#### Evidence fragments (exact text from the supplied record)")
    fragments = json.loads(row["evidence_fragments_json"])
    if fragments:
        by_group: dict[str, list[dict]] = {}
        for f in fragments:
            by_group.setdefault(f["group"], []).append(f)
        for group, frags in by_group.items():
            icon = "🔴" if group == "negation" else "🔎"
            with st.expander(f"{icon} {group} ({len(frags)} fragment(s))", expanded=group == "negation"):
                for f in frags:
                    st.markdown(f"*from `{f['field']}`:*")
                    st.code(f["text"], language=None)
    else:
        st.caption(
            "No ICU-related text found in this record. This means the record shows no "
            "evidence - not that the facility has been verified to lack an ICU."
        )


def main() -> None:
    st.title("🏥 CareGap Map - ICU coverage trust layer")
    st.caption(
        "Distinguishes likely medical gaps from data deserts. All signals reflect **dataset "
        "consistency, not verified clinical capability** - 'no reliable ICU evidence' is never "
        "treated as 'no ICU exists'."
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

    # ---------------- Sidebar: capability + region selection ----------------
    with st.sidebar:
        st.selectbox("Capability", ["ICU"], disabled=True, help="This milestone supports ICU only.")
        states = sorted(s for s in scored["state_final"].dropna().unique())
        state = st.selectbox("State", ["All India"] + states + [UNASSIGNED])
        district = None
        if state not in ("All India", UNASSIGNED):
            districts = sorted(
                d
                for d in scored.loc[scored["state_final"] == state, "district_final"]
                .fillna(UNASSIGNED)
                .unique()
            )
            district = st.selectbox("District (optional)", ["All districts"] + districts)
            if district == "All districts":
                district = None
        st.divider()
        with st.expander("Active thresholds"):
            t = config.thresholds
            st.markdown(
                f"- judgeable if completeness ≥ **{t.sufficient_completeness}**\n"
                f"- trusted if evidence ≥ **{t.high_evidence}**\n"
                f"- likely gap if evidence ≤ **{t.low_evidence}**\n"
                f"- region data desert below **{t.region_min_data_pct:.0f}%** judgeable "
                f"or **{t.region_min_facilities}** records"
            )
            st.caption("Configurable via CAREGAP_SCORING_CONFIG - see DECISIONS.md.")

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

    # ---------------- Regional summary ----------------
    st.header(f"Regional evidence - {region_label}")
    summary = summarize_facilities(subset, config)
    status_banner(summary["region_status"], summary["region_status_reason"])
    st.caption(f"⚠️ {REGION_DISCLAIMER}")
    if state == "All India" or not district:
        st.caption("State-level numbers are high-level summaries — select a district for the planning view.")
    metrics_row(summary)

    if summary["region_status"] == REGION_DATA_DESERT and summary["facility_count"] > 0:
        st.caption(
            "⚠️ This region is a **data desert**: the records are too thin to judge. "
            "Treat it as *unknown*, not as a confirmed ICU gap."
        )

    # ---------------- Regional chart ----------------
    if state == "All India":
        classification_chart(
            scored.assign(region=scored["state_final"].fillna(UNASSIGNED)),
            "region",
            "Facility classifications by state",
        )
    elif not district and len(subset):
        classification_chart(
            subset.assign(region=subset["district_final"].fillna(UNASSIGNED)),
            "region",
            f"Facility classifications by district - {state}",
        )

    # ---------------- Facility map (optional; table stays primary) ----------
    map_selected_id: str | None = None
    with st.expander("🗺️ Facility evidence map (beta)", expanded=False):
        map_selected_id = facility_map(subset)

    # ---------------- Facility table ----------------
    st.header("Facilities behind this result")
    class_filter = st.multiselect(
        "Filter by classification",
        CLASS_STACK_ORDER,
        default=CLASS_STACK_ORDER,
        format_func=lambda c: f"{CLASS_ICONS[c]} {c}",
    )
    table = subset[subset["classification"].isin(class_filter)].sort_values(
        ["capability_evidence_score", "data_completeness_score"], ascending=False
    )
    st.dataframe(
        table[
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
        ].rename(
            columns={
                "address_city": "city",
                "district_final": "district",
                "state_final": "state",
                "capability_evidence_score": "evidence 0-100",
                "data_completeness_score": "completeness 0-100",
                "n_validation_flags": "flags",
            }
        ),
        hide_index=True,
        width="stretch",
        height=320,
    )

    # ---------------- Facility drilldown ----------------
    st.header("Facility drilldown")
    if table.empty:
        st.info("No facilities match the current selection.")
    else:
        options = {
            f"{r['name']} - {r.get('address_city') or '?'} ({r['unique_id'][:8]})": idx
            for idx, r in table.iterrows()
        }
        default_index = 0
        if map_selected_id is not None:
            ids = table["unique_id"].tolist()
            if map_selected_id in ids:
                default_index = ids.index(map_selected_id)
        choice = st.selectbox("Inspect a facility", list(options.keys()), index=default_index)
        row = table.loc[options[choice]]
        facility_detail(row)
        st.divider()
        notes_panel(
            "facility",
            row["unique_id"],
            "Notes persist in the configured review store (SQLite locally, "
            "Delta table on Databricks) and survive page refreshes.",
        )

    # ---------------- Region-level notes ----------------
    if state != "All India":
        st.divider()
        if district and district != UNASSIGNED:
            notes_panel("district", f"{state}/{district}", "Attached to the selected district.")
        elif state != UNASSIGNED:
            notes_panel("state", state, "Attached to the selected state.")


main()
