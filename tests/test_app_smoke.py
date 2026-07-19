"""Smoke test: the Streamlit app renders against real processed data.

Skipped when the processed Parquet outputs are absent (e.g. fresh clone
without the raw challenge data), so the unit suite stays self-contained.
"""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "app.py"
PROCESSED = Path(__file__).resolve().parents[1] / "data" / "processed"

needs_data = pytest.mark.skipif(
    not (PROCESSED / "facilities_scored.parquet").exists(),
    reason="processed data not built; run scripts/build_processed_data.py",
)


@needs_data
def test_app_renders_all_india_without_exception():
    at = AppTest.from_file(str(APP), default_timeout=120)
    at.run()
    assert not at.exception, at.exception
    # Planner-first metric cards (D24) plus the technical metrics that moved
    # into the "How this regional assessment was calculated" expander.
    labels = " ".join(m.label for m in at.metric)
    for expected in (
        "Facility records",
        "Judgeable records",
        "Trusted ICU evidence",
        "Needs verification",
        "Trust-weighted ICU evidence index",
        "Trusted-record share",
    ):
        assert expected in labels
    # Renamed headline metrics (D19): no "coverage" wording for record shares.
    assert "Evidence coverage" not in labels
    assert "Trust-weighted ICU coverage" not in labels
    assert "Likely gap" not in labels


@needs_data
def test_full_facility_table_and_workflow_sections_present():
    """D24: the full table stays available; the four-step workflow renders."""
    at = AppTest.from_file(str(APP), default_timeout=120)
    at.run()
    assert not at.exception, at.exception
    page_text = (
        " ".join(m.value for m in at.markdown)
        + " ".join(h.value for h in at.header)
        + " ".join(e.label for e in at.expander)
    )
    assert "View all" in page_text and "facility records" in page_text
    assert "Review priority facilities" in page_text
    assert "Save a planning scenario" in page_text
    assert "Why this status?" in page_text
    assert "Recommended next action" in page_text
    # Exact evidence and notes remain reachable in the drilldown.
    assert "Exact evidence" in page_text
    assert any(ta.label == "New note" for ta in at.text_area)
    # No threshold controls exist in the planner UI (policy is read-only).
    assert not at.slider
    assert not at.number_input


@needs_data
def test_state_selection_updates_summary():
    at = AppTest.from_file(str(APP), default_timeout=120)
    at.run()
    state_box = next(sb for sb in at.selectbox if sb.label == "State")
    state_box.select("Kerala")
    at.run()
    assert not at.exception, at.exception
    headers = " ".join(h.value for h in at.header)
    assert "Kerala" in headers


@needs_data
def test_hero_counts_render_without_markdown_artifacts():
    """Regression: the hero count line is raw HTML - literal ** markers must
    never appear, in the All India view or a district view."""

    def hero_counts(at: AppTest) -> str:
        return next(m.value for m in at.markdown if '<div class="cg-counts">' in str(m.value))

    at = AppTest.from_file(str(APP), default_timeout=120)
    at.run()
    counts = hero_counts(at)
    assert "**" not in counts
    assert "<strong>" in counts

    next(sb for sb in at.selectbox if sb.label == "State").select("Kerala")
    at.run()
    district_box = next(sb for sb in at.selectbox if sb.label == "District (optional)")
    district_box.select(district_box.options[1])
    at.run()
    assert not at.exception, at.exception
    counts = hero_counts(at)
    assert "**" not in counts
    assert "<strong>" in counts


@needs_data
def test_prototype_scope_capability_control():
    """The capability control shows the ICU prototype scope and nothing else."""
    at = AppTest.from_file(str(APP), default_timeout=120)
    at.run()
    assert not at.exception, at.exception
    capability = next(sb for sb in at.selectbox if sb.label == "Capability")
    assert capability.options == ["ICU — prototype scope"]
    assert capability.disabled is True


@needs_data
def test_facility_and_regional_counts_unchanged_by_operational_view():
    """D23 is descriptive only: stored classifications and regional statuses
    keep the exact pre-change distribution."""
    import pandas as pd

    scored = pd.read_parquet(PROCESSED / "facilities_scored.parquet")
    counts = scored["classification"].value_counts()
    assert counts["Trusted ICU Coverage"] == 203
    assert counts["Needs Human Review"] == 2867
    assert counts["Likely Medical Gap"] == 6890
    assert counts["Insufficient Data"] == 117

    districts = pd.read_parquet(PROCESSED / "region_summary_district.parquet")
    status = districts["region_status"].value_counts()
    assert status["Trusted ICU evidence found"] == 103
    assert status["Needs facility verification"] == 256
    assert status["Potential planning gap"] == 32
    assert status["Insufficient data to assess"] == 186


@needs_data
def test_query_params_restore_region_after_refresh():
    """Fix A: ?state=...&district=... survives a fresh session (page refresh)."""
    at = AppTest.from_file(str(APP), default_timeout=120)
    at.query_params["state"] = "Kerala"
    at.run()
    assert not at.exception, at.exception
    assert next(sb for sb in at.selectbox if sb.label == "State").value == "Kerala"

    # And with a district: pick a real one from the first session.
    district = next(sb for sb in at.selectbox if sb.label == "District (optional)").options[1]
    at2 = AppTest.from_file(str(APP), default_timeout=120)
    at2.query_params["state"] = "Kerala"
    at2.query_params["district"] = district
    at2.run()
    assert not at2.exception, at2.exception
    assert next(sb for sb in at2.selectbox if sb.label == "District (optional)").value == district


@needs_data
def test_invalid_query_params_fall_back_to_all_india():
    at = AppTest.from_file(str(APP), default_timeout=120)
    at.query_params["state"] = "Atlantis"
    at.query_params["district"] = "Nowhere"
    at.run()
    assert not at.exception, at.exception
    assert next(sb for sb in at.selectbox if sb.label == "State").value == "All India"


@needs_data
def test_selection_is_reflected_in_query_params():
    at = AppTest.from_file(str(APP), default_timeout=120)
    at.run()
    next(sb for sb in at.selectbox if sb.label == "State").select("Kerala")
    at.run()
    assert not at.exception, at.exception
    # The AppTest accessor returns list-valued params.
    assert at.query_params.get("state") in ("Kerala", ["Kerala"])


@needs_data
def test_saved_note_appears_after_rerun():
    """Fix B regression: a saved note is listed on the very next render."""
    import sqlite3

    marker = "AUTOTEST NOTE - safe to delete"
    at = AppTest.from_file(str(APP), default_timeout=120)
    at.run()
    try:
        note_area = next(ta for ta in at.text_area if ta.label == "New note")
        note_area.input(marker)
        next(b for b in at.button if getattr(b, "label", "") == "Save note").click()
        at.run()
        assert not at.exception, at.exception
        rendered = " ".join(m.value for m in at.markdown)
        assert marker in rendered
    finally:
        with sqlite3.connect(Path("data") / "reviews.db") as conn:
            conn.execute("DELETE FROM review_notes WHERE note = ?", (marker,))


@needs_data
def test_scenario_acceptance_save_reopen_restart():
    """Acceptance: select a district, save a named scenario, refresh, reopen,
    verify the filters and snapshot, 'restart', verify it survived."""
    from caregap_map.scenarios import SqliteScenarioStore

    store = SqliteScenarioStore(Path("data") / "reviews.db")
    before_ids = {s.id for s in store.list_scenarios()}

    # --- select a district and save a named scenario -----------------------
    at = AppTest.from_file(str(APP), default_timeout=120)
    at.run()
    next(sb for sb in at.selectbox if sb.label == "State").select("Kerala")
    at.run()
    district_box = next(sb for sb in at.selectbox if sb.label == "District (optional)")
    district = district_box.options[1]
    district_box.select(district)
    at.run()
    next(ti for ti in at.text_input if ti.label == "Scenario name").input("Acceptance scenario")
    next(b for b in at.button if getattr(b, "label", "") == "Save scenario").click()
    at.run()
    assert not at.exception, at.exception

    created = [s for s in store.list_scenarios() if s.id not in before_ids]
    assert len(created) == 1
    saved = created[0]
    try:
        assert saved.state == "Kerala" and saved.district == district
        assert saved.facility_count > 0
        assert saved.data_snapshot and saved.scoring_config_hash

        # --- refresh (new session), reopen, verify the filters -------------
        at2 = AppTest.from_file(str(APP), default_timeout=120)
        at2.run()
        scenario_box = next(sb for sb in at2.selectbox if sb.label == "Scenario")
        option = next(o for o in scenario_box.options if o.startswith("Acceptance scenario"))
        scenario_box.select(option)
        at2.run()
        at2.button(key=f"reopen_{saved.id}").click()
        at2.run()
        assert not at2.exception, at2.exception
        assert next(sb for sb in at2.selectbox if sb.label == "State").value == "Kerala"
        assert (
            next(sb for sb in at2.selectbox if sb.label == "District (optional)").value == district
        )

        # --- 'restart': a brand-new store instance still has the scenario --
        assert SqliteScenarioStore(Path("data") / "reviews.db").get_scenario(saved.id) is not None
    finally:
        store.delete_scenario(saved.id)
