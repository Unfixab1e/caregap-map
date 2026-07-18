"""Data-source factory and the Databricks adapter (stubbed connection)."""

import pyarrow as pa
import pytest

from caregap_map.data_access import (
    DatabricksDataSource,
    LocalDataSource,
    MissingDataError,
    get_data_source,
)


class StubCursor:
    def __init__(self, log):
        self.log = log

    def execute(self, sql):
        self.log.append(sql)

    def fetchall_arrow(self):
        return pa.table({"state": ["Kerala"], "facility_count": [10]})

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class StubConnection:
    def __init__(self, log):
        self.log = log

    def cursor(self):
        return StubCursor(self.log)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestFactory:
    def test_default_is_local(self, monkeypatch):
        monkeypatch.delenv("CAREGAP_DATA_SOURCE", raising=False)
        assert isinstance(get_data_source(), LocalDataSource)

    def test_databricks_mode(self, monkeypatch):
        monkeypatch.setenv("CAREGAP_DATA_SOURCE", "databricks")
        monkeypatch.setenv("DATABRICKS_HOST", "https://example.cloud.databricks.com")
        monkeypatch.setenv("DATABRICKS_HTTP_PATH", "/sql/1.0/warehouses/abc")
        monkeypatch.setenv("DATABRICKS_TOKEN", "dapi-test")
        assert isinstance(get_data_source(), DatabricksDataSource)

    def test_unknown_mode_rejected(self, monkeypatch):
        monkeypatch.setenv("CAREGAP_DATA_SOURCE", "excel")
        with pytest.raises(ValueError):
            get_data_source()


class TestDatabricksDataSource:
    def test_missing_credentials_fail_fast(self, monkeypatch):
        for var in ("DATABRICKS_HOST", "DATABRICKS_HTTP_PATH", "DATABRICKS_TOKEN"):
            monkeypatch.delenv(var, raising=False)
        with pytest.raises(MissingDataError):
            DatabricksDataSource()

    def test_identifiers_validated(self):
        with pytest.raises(ValueError):
            DatabricksDataSource(catalog="bad;drop", schema="caregap", connection_factory=object)

    def test_reads_table_through_connection(self):
        log: list[str] = []
        source = DatabricksDataSource(
            catalog="main", schema="caregap", connection_factory=lambda: StubConnection(log)
        )
        df = source.load_region_summary("state")
        assert df["state"].tolist() == ["Kerala"]
        assert log == ["SELECT * FROM `main`.`caregap`.`region_summary_state`"]

    def test_unknown_region_level_rejected(self):
        source = DatabricksDataSource(
            catalog="main", schema="caregap", connection_factory=lambda: StubConnection([])
        )
        with pytest.raises(ValueError):
            source.load_region_summary("country")
