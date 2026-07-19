"""Planning scenarios: model, SQLite/Delta persistence, factory, helpers."""

from __future__ import annotations

import pytest

from caregap_map.config import ScoringConfig
from caregap_map.scenarios import (
    DeltaScenarioStore,
    PlanningScenario,
    SqliteScenarioStore,
    get_scenario_store,
    scenario_from_summary,
    scoring_config_fingerprint,
)


def scenario(**overrides) -> PlanningScenario:
    base = {
        "name": "Ernakulam ICU review",
        "author": "planner",
        "state": "Kerala",
        "district": "Ernakulam",
        "region_status": "Trusted ICU evidence found",
        "facility_count": 42,
        "trusted_count": 3,
        "needs_review_count": 10,
        "no_icu_evidence_count": 25,
        "insufficient_data_count": 4,
        "judgeable_pct": 95.2,
        "trusted_record_share_pct": 7.1,
        "trust_weighted_evidence_index": 0.21,
        "selected_facility_ids": ["abc", "def"],
        "note": "verify the two flagged hospitals",
        "scoring_config_hash": "deadbeef1234",
        "data_snapshot": "10077 records / cafe01234567",
    }
    base.update(overrides)
    return PlanningScenario(**base)


class TestSqliteScenarioStore:
    def test_roundtrip_and_persistence_across_instances(self, tmp_path):
        db = tmp_path / "reviews.db"
        store = SqliteScenarioStore(db)
        saved = store.save_scenario(scenario())
        assert saved.id and saved.created_at

        # A NEW store instance on the same file sees the scenario (restart).
        reopened_store = SqliteScenarioStore(db)
        listed = reopened_store.list_scenarios()
        assert [s.id for s in listed] == [saved.id]
        got = reopened_store.get_scenario(saved.id)
        assert got is not None
        assert got.name == "Ernakulam ICU review"
        assert got.state == "Kerala" and got.district == "Ernakulam"
        assert got.selected_facility_ids == ["abc", "def"]
        assert got.judgeable_pct == pytest.approx(95.2)
        assert got.trust_weighted_evidence_index == pytest.approx(0.21)
        assert got.scoring_config_hash == "deadbeef1234"

    def test_all_india_scenario_round_trips_null_region(self, tmp_path):
        store = SqliteScenarioStore(tmp_path / "r.db")
        saved = store.save_scenario(scenario(state=None, district=None))
        got = store.get_scenario(saved.id)
        assert got.state is None and got.district is None
        assert got.region_label == "All India"

    def test_delete(self, tmp_path):
        store = SqliteScenarioStore(tmp_path / "r.db")
        saved = store.save_scenario(scenario())
        assert store.delete_scenario(saved.id) is True
        assert store.get_scenario(saved.id) is None
        assert store.delete_scenario(saved.id) is False  # already gone

    def test_empty_name_rejected(self, tmp_path):
        store = SqliteScenarioStore(tmp_path / "r.db")
        with pytest.raises(ValueError):
            store.save_scenario(scenario(name="   "))
        assert store.list_scenarios() == []

    def test_notes_and_scenarios_share_the_db_file(self, tmp_path):
        from caregap_map.persistence import ReviewNote, SqliteReviewStore

        db = tmp_path / "reviews.db"
        SqliteReviewStore(db).add_note(ReviewNote(scope_type="state", scope_id="Kerala", note="x"))
        store = SqliteScenarioStore(db)
        store.save_scenario(scenario())
        assert len(store.list_scenarios()) == 1


class StubCursor:
    def __init__(self, log, rows):
        self.log = log
        self.rows = rows

    def execute(self, sql, parameters=None):
        self.log.append((sql, parameters or {}))

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class StubConnection:
    def __init__(self, log, rows):
        self.log = log
        self.rows = rows

    def cursor(self):
        return StubCursor(self.log, self.rows)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def make_delta_store(rows=()):
    log: list[tuple[str, dict]] = []
    store = DeltaScenarioStore(
        catalog="main",
        schema="caregap",
        connection_factory=lambda: StubConnection(log, list(rows)),
    )
    return store, log


def delta_row(saved: PlanningScenario | None = None) -> tuple:
    s = saved or scenario()
    return (
        s.id or "id1",
        s.created_at or "2026-07-19T09:00:00+00:00",
        s.author,
        s.name,
        s.capability,
        s.state,
        s.district,
        s.region_status,
        s.facility_count,
        s.trusted_count,
        s.needs_review_count,
        s.no_icu_evidence_count,
        s.insufficient_data_count,
        s.judgeable_pct,
        s.trusted_record_share_pct,
        s.trust_weighted_evidence_index,
        '["abc", "def"]',
        s.note,
        s.scoring_config_hash,
        s.data_snapshot,
    )


class TestDeltaScenarioStore:
    def test_table_created_on_init(self):
        _, log = make_delta_store()
        assert "CREATE TABLE IF NOT EXISTS `main`.`caregap`.`planning_scenarios`" in log[0][0]

    def test_save_uses_parameters_never_interpolation(self):
        store, log = make_delta_store()
        malicious = "'); DROP TABLE planning_scenarios; --"
        saved = store.save_scenario(scenario(name=malicious, note=malicious))
        sql, params = log[-1]
        assert "INSERT INTO `main`.`caregap`.`planning_scenarios`" in sql
        assert ":name" in sql and ":note" in sql
        assert malicious not in sql
        assert params["name"] == malicious.strip()
        assert params["note"] == malicious
        assert saved.id and saved.created_at

    def test_list_orders_by_created_at(self):
        store, log = make_delta_store(rows=[delta_row()])
        listed = store.list_scenarios()
        assert "ORDER BY created_at DESC" in log[-1][0]
        assert listed[0].selected_facility_ids == ["abc", "def"]

    def test_get_and_delete_are_parameterized(self):
        store, log = make_delta_store(rows=[delta_row()])
        got = store.get_scenario("id1")
        assert got is not None
        assert log[-1][1] == {"id": "id1"}
        assert store.delete_scenario("id1") is True
        sql, params = log[-1]
        assert sql.startswith("DELETE FROM")
        assert params == {"id": "id1"}

    def test_delete_missing_returns_false(self):
        store, _ = make_delta_store(rows=[])
        assert store.delete_scenario("nope") is False

    def test_identifier_validation(self):
        with pytest.raises(ValueError):
            DeltaScenarioStore(catalog="bad;drop", schema="caregap", connection_factory=object)

    def test_missing_credentials_fail_fast(self, monkeypatch):
        for var in ("DATABRICKS_HOST", "DATABRICKS_HTTP_PATH", "DATABRICKS_TOKEN"):
            monkeypatch.delenv(var, raising=False)
        with pytest.raises(RuntimeError):
            DeltaScenarioStore()


class TestFactory:
    def test_default_is_sqlite(self, monkeypatch, tmp_path):
        monkeypatch.delenv("CAREGAP_SCENARIO_STORE", raising=False)
        monkeypatch.delenv("CAREGAP_REVIEW_STORE", raising=False)
        monkeypatch.setenv("CAREGAP_DATA_DIR", str(tmp_path))
        assert isinstance(get_scenario_store(), SqliteScenarioStore)

    def test_follows_review_store_backend(self, monkeypatch, tmp_path):
        monkeypatch.delenv("CAREGAP_SCENARIO_STORE", raising=False)
        monkeypatch.setenv("CAREGAP_REVIEW_STORE", "sqlite")
        monkeypatch.setenv("CAREGAP_DATA_DIR", str(tmp_path))
        assert isinstance(get_scenario_store(), SqliteScenarioStore)

    def test_unknown_mode_rejected(self, monkeypatch):
        monkeypatch.setenv("CAREGAP_SCENARIO_STORE", "postgres")
        with pytest.raises(ValueError):
            get_scenario_store()


class TestHelpers:
    def test_scenario_from_summary_maps_metric_keys(self):
        summary = {
            "region_status": "Potential planning gap",
            "facility_count": 7,
            "trusted_icu_count": 0,
            "needs_review_count": 0,
            "likely_gap_count": 6,
            "insufficient_data_count": 1,
            "pct_sufficient_data": 85.7,
            "evidence_coverage_pct": 0.0,
            "trust_weighted_icu_coverage": 0.04,
        }
        s = scenario_from_summary(
            name="gap district",
            summary=summary,
            state="Kerala",
            district="Idukki",
            scoring_config_hash="abc",
            data_snapshot="7 records / 123",
        )
        assert s.no_icu_evidence_count == 6
        assert s.trusted_record_share_pct == 0.0
        assert s.trust_weighted_evidence_index == pytest.approx(0.04)
        assert s.judgeable_pct == pytest.approx(85.7)
        assert s.region_label == "Kerala / Idukki"

    def test_scoring_config_fingerprint_tracks_changes(self):
        base = scoring_config_fingerprint(ScoringConfig())
        changed = ScoringConfig()
        changed.thresholds.high_evidence = 60
        assert scoring_config_fingerprint(ScoringConfig()) == base  # stable
        assert scoring_config_fingerprint(changed) != base

    def test_data_snapshot_id_tracks_class_counts(self):
        import pandas as pd

        from caregap_map.scenarios import data_snapshot_id

        a = pd.DataFrame({"classification": ["Trusted ICU Coverage", "Likely Medical Gap"]})
        b = pd.DataFrame({"classification": ["Trusted ICU Coverage", "Trusted ICU Coverage"]})
        assert data_snapshot_id(a) != data_snapshot_id(b)
        assert data_snapshot_id(a).startswith("2 records / ")
