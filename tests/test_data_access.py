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


class StubFilesClient:
    """Mimics WorkspaceClient().files for the volume source."""

    def __init__(self, payloads: dict[str, bytes]):
        self.payloads = payloads
        self.downloaded: list[str] = []

    def download(self, path: str):
        import io
        from types import SimpleNamespace

        self.downloaded.append(path)
        name = path.rsplit("/", 1)[-1]
        if name not in self.payloads:
            raise FileNotFoundError(path)
        return SimpleNamespace(contents=io.BytesIO(self.payloads[name]))


class TestVolumeDataSource:
    def _parquet_bytes(self) -> bytes:
        import io

        import pandas as pd

        buf = io.BytesIO()
        pd.DataFrame({"state": ["Kerala"], "facility_count": [3]}).to_parquet(buf)
        return buf.getvalue()

    def test_downloads_from_volume_then_reads_locally(self, tmp_path):
        from caregap_map.data_access import VolumeDataSource

        stub = StubFilesClient({"region_summary_state.parquet": self._parquet_bytes()})
        source = VolumeDataSource(
            volume_dir="/Volumes/workspace/caregap/caregap_data",
            cache_dir=tmp_path,
            files_client_factory=lambda: stub,
        )
        df = source.load_region_summary("state")
        assert df["state"].tolist() == ["Kerala"]
        assert stub.downloaded == [
            "/Volumes/workspace/caregap/caregap_data/processed/region_summary_state.parquet"
        ]
        # Second read hits the local cache - no second download.
        source.load_region_summary("state")
        assert len(stub.downloaded) == 1

    def test_missing_volume_file_raises_actionable_error(self, tmp_path):
        from caregap_map.data_access import VolumeDataSource

        source = VolumeDataSource(
            volume_dir="/Volumes/workspace/caregap/caregap_data",
            cache_dir=tmp_path,
            files_client_factory=lambda: StubFilesClient({}),
        )
        with pytest.raises(MissingDataError, match="Files API"):
            source.load_scored_facilities()

    def test_non_volume_path_rejected(self, tmp_path):
        from caregap_map.data_access import VolumeDataSource

        with pytest.raises(MissingDataError):
            VolumeDataSource(volume_dir="data", cache_dir=tmp_path)


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
