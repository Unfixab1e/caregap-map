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
    # The four-state metrics row is on screen, with the precise display labels.
    labels = " ".join(m.label for m in at.metric)
    for expected in ("Trusted ICU evidence", "No ICU evidence", "Insufficient data", "Needs review"):
        assert expected in labels
    # Renamed headline metrics (D19): no "coverage" wording for record shares.
    assert "Trust-weighted ICU evidence index" in labels
    assert "Trusted-record share" in labels
    assert "Evidence coverage" not in labels
    assert "Trust-weighted ICU coverage" not in labels
    assert "Likely gap" not in labels


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
