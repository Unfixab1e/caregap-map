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
