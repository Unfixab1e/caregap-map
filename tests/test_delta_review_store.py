"""Delta-backed review store: parameterised SQL, filtering, factory selection."""

import pytest

from caregap_map.persistence import (
    DeltaReviewStore,
    ReviewNote,
    SqliteReviewStore,
    get_review_store,
)


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


def make_store(rows=()):
    log: list[tuple[str, dict]] = []
    store = DeltaReviewStore(
        catalog="main",
        schema="caregap",
        connection_factory=lambda: StubConnection(log, list(rows)),
    )
    return store, log


class TestDeltaReviewStore:
    def test_table_created_on_init(self):
        _, log = make_store()
        assert "CREATE TABLE IF NOT EXISTS `main`.`caregap`.`review_notes`" in log[0][0]

    def test_add_note_uses_parameters_never_interpolation(self):
        store, log = make_store()
        malicious = "'); DROP TABLE review_notes; --"
        saved = store.add_note(ReviewNote(scope_type="district", scope_id="Kerala/Ernakulam", note=malicious))
        sql, params = log[-1]
        assert "INSERT INTO `main`.`caregap`.`review_notes`" in sql
        assert ":note" in sql
        assert malicious not in sql  # user text only travels as a parameter
        assert params["note"] == malicious
        assert saved.id and saved.created_at

    def test_list_notes_filters_and_orders(self):
        rows = [("id1", "2026-07-18T10:00:00+00:00", "district", "Kerala/Ernakulam", "check", "n")]
        store, log = make_store(rows)
        notes = store.list_notes(scope_type="district", scope_id="Kerala/Ernakulam")
        sql, params = log[-1]
        assert "WHERE scope_type = :scope_type AND scope_id = :scope_id" in sql
        assert "ORDER BY created_at DESC" in sql
        assert params == {"scope_type": "district", "scope_id": "Kerala/Ernakulam"}
        assert notes[0].note == "check"

    def test_empty_note_rejected_before_any_sql(self):
        store, log = make_store()
        n_before = len(log)
        with pytest.raises(ValueError):
            store.add_note(ReviewNote(scope_type="facility", scope_id="f1", note="  "))
        assert len(log) == n_before

    def test_invalid_scope_rejected(self):
        store, _ = make_store()
        with pytest.raises(ValueError):
            store.add_note(ReviewNote(scope_type="country", scope_id="India", note="x"))

    def test_identifier_validation(self):
        with pytest.raises(ValueError):
            DeltaReviewStore(catalog="bad;drop", schema="caregap", connection_factory=object)

    def test_missing_credentials_fail_fast(self, monkeypatch):
        for var in ("DATABRICKS_HOST", "DATABRICKS_HTTP_PATH", "DATABRICKS_TOKEN"):
            monkeypatch.delenv(var, raising=False)
        with pytest.raises(RuntimeError):
            DeltaReviewStore()


class TestReviewStoreFactory:
    def test_default_is_sqlite(self, monkeypatch, tmp_path):
        monkeypatch.delenv("CAREGAP_REVIEW_STORE", raising=False)
        monkeypatch.setenv("CAREGAP_DATA_DIR", str(tmp_path))
        assert isinstance(get_review_store(), SqliteReviewStore)

    def test_unknown_mode_rejected(self, monkeypatch):
        monkeypatch.setenv("CAREGAP_REVIEW_STORE", "postgres")
        with pytest.raises(ValueError):
            get_review_store()
