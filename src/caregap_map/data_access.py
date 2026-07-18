"""Data source adapters.

The application talks to a :class:`DataSource` protocol, never to file
paths or Databricks APIs directly. The local adapter reads the raw CSVs
and processed Parquet files; a Databricks adapter can implement the same
protocol against Unity Catalog tables without touching scoring or UI code.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import pandas as pd

from .config import DataPaths, default_paths
from .databricks_connect import connect_warehouse, have_warehouse_credentials, resolve_http_path


class DataSource(Protocol):
    """What the app and pipeline need from any storage backend."""

    def load_facilities_raw(self) -> pd.DataFrame: ...

    def load_pin_directory_raw(self) -> pd.DataFrame: ...

    def load_nfhs_raw(self) -> pd.DataFrame: ...

    def load_scored_facilities(self) -> pd.DataFrame: ...

    def load_region_summary(self, level: str) -> pd.DataFrame: ...


class MissingDataError(FileNotFoundError):
    """A required dataset is absent; carries a actionable message."""


class LocalDataSource:
    """Reads raw CSVs and processed Parquet files from the local data dir."""

    def __init__(self, paths: DataPaths | None = None) -> None:
        self.paths = paths or default_paths()

    def _require(self, path, hint: str) -> None:
        if not path.exists():
            raise MissingDataError(f"Required file not found: {path}. {hint}")

    def load_facilities_raw(self) -> pd.DataFrame:
        self._require(self.paths.facilities_csv, "Place facilities.csv under data/raw/.")
        return pd.read_csv(self.paths.facilities_csv, dtype=str)

    def load_pin_directory_raw(self) -> pd.DataFrame:
        self._require(self.paths.pin_directory_csv, "Place india_post_pincode_directory.csv under data/raw/.")
        return pd.read_csv(self.paths.pin_directory_csv, dtype=str)

    def load_nfhs_raw(self) -> pd.DataFrame:
        self._require(self.paths.nfhs_csv, "Place nfhs_5_district_health_indicators.csv under data/raw/.")
        return pd.read_csv(self.paths.nfhs_csv, dtype=str)

    def load_scored_facilities(self) -> pd.DataFrame:
        self._require(
            self.paths.facilities_scored_parquet,
            "Run `python scripts/build_processed_data.py` first.",
        )
        return pd.read_parquet(self.paths.facilities_scored_parquet)

    def load_region_summary(self, level: str) -> pd.DataFrame:
        path = self.paths.region_state_parquet if level == "state" else self.paths.region_district_parquet
        self._require(path, "Run `python scripts/build_processed_data.py` first.")
        return pd.read_parquet(path)


# Logical dataset -> Unity Catalog table name (see scripts/register_tables.sql).
DATABRICKS_TABLES = {
    "facilities_raw": "facilities_raw",
    "pin_directory_raw": "pin_directory_raw",
    "nfhs_raw": "nfhs_raw",
    "facilities_scored": "facilities_scored",
    "region_summary_state": "region_summary_state",
    "region_summary_district": "region_summary_district",
}

_IDENTIFIER = re.compile(r"^[A-Za-z0-9_]+$")


class DatabricksDataSource:
    """Reads the same logical tables from Unity Catalog via a SQL warehouse.

    Connection settings come from arguments or the standard environment
    variables ``DATABRICKS_HOST``, ``DATABRICKS_HTTP_PATH``,
    ``DATABRICKS_TOKEN`` plus ``CAREGAP_DATABRICKS_CATALOG`` /
    ``CAREGAP_DATABRICKS_SCHEMA``. See DEPLOYMENT.md for table registration.

    ``connection_factory`` is injectable for tests; by default a
    ``databricks-sql-connector`` connection is opened per query (the
    connector keeps sessions cheap and the app caches results anyway).
    """

    def __init__(
        self,
        catalog: str | None = None,
        schema: str | None = None,
        host: str | None = None,
        http_path: str | None = None,
        token: str | None = None,
        connection_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.catalog = catalog or os.environ.get("CAREGAP_DATABRICKS_CATALOG", "main")
        self.schema = schema or os.environ.get("CAREGAP_DATABRICKS_SCHEMA", "caregap")
        self._host = host or os.environ.get("DATABRICKS_HOST", "")
        self._http_path = resolve_http_path(http_path)
        self._token = token or os.environ.get("DATABRICKS_TOKEN", "")
        self._connection_factory = connection_factory
        for name, value in (("catalog", self.catalog), ("schema", self.schema)):
            if not _IDENTIFIER.match(value):
                raise ValueError(f"Invalid Databricks {name} identifier: {value!r}")
        if connection_factory is None and not (
            self._http_path and have_warehouse_credentials(self._host, self._token)
        ):
            raise MissingDataError(
                "DatabricksDataSource needs DATABRICKS_HOST plus a warehouse HTTP path "
                "(DATABRICKS_HTTP_PATH or DATABRICKS_WAREHOUSE_ID) and either "
                "DATABRICKS_TOKEN or app service-principal OAuth (see DEPLOYMENT.md)."
            )

    def _connect(self):
        if self._connection_factory is not None:
            return self._connection_factory()
        return connect_warehouse(self._host, self._http_path, self._token)

    def _read_table(self, dataset: str) -> pd.DataFrame:
        table = DATABRICKS_TABLES[dataset]
        with self._connect() as conn, conn.cursor() as cursor:
            cursor.execute(f"SELECT * FROM `{self.catalog}`.`{self.schema}`.`{table}`")
            return cursor.fetchall_arrow().to_pandas()

    def load_facilities_raw(self) -> pd.DataFrame:
        return self._read_table("facilities_raw")

    def load_pin_directory_raw(self) -> pd.DataFrame:
        return self._read_table("pin_directory_raw")

    def load_nfhs_raw(self) -> pd.DataFrame:
        return self._read_table("nfhs_raw")

    def load_scored_facilities(self) -> pd.DataFrame:
        return self._read_table("facilities_scored")

    def load_region_summary(self, level: str) -> pd.DataFrame:
        if level not in ("state", "district"):
            raise ValueError(f"Unknown region level: {level!r}")
        return self._read_table(f"region_summary_{level}")


class VolumeDataSource(LocalDataSource):
    """Reads processed Parquet from a Unity Catalog volume via the Files API.

    Databricks Apps containers do NOT mount ``/Volumes`` as a filesystem
    (unlike notebooks/clusters), so the volume files are downloaded once into
    a local cache directory using the app service principal's injected
    credentials, then read like any local data. New uploads are picked up on
    app restart (the cache lives on the ephemeral app filesystem).
    """

    _FILES = (
        "facilities_scored.parquet",
        "region_summary_state.parquet",
        "region_summary_district.parquet",
    )

    def __init__(
        self,
        volume_dir: str | None = None,
        cache_dir: str | Path | None = None,
        files_client_factory: Callable[[], Any] | None = None,
    ) -> None:
        volume = volume_dir or os.environ.get("CAREGAP_DATA_DIR", "")
        if not volume.startswith("/Volumes/"):
            raise MissingDataError(
                f"CAREGAP_DATA_SOURCE=volume needs CAREGAP_DATA_DIR to point at a "
                f"/Volumes/... path, got {volume!r}."
            )
        cache = Path(cache_dir or os.environ.get("CAREGAP_VOLUME_CACHE", ".volume_cache"))
        super().__init__(DataPaths(data_dir=cache))
        self._volume_dir = volume.rstrip("/")
        self._files_client_factory = files_client_factory

    def _files_client(self):
        if self._files_client_factory is not None:
            return self._files_client_factory()
        try:
            from databricks.sdk import WorkspaceClient
        except ImportError as exc:
            raise ImportError(
                "The 'databricks-sdk' package is required for the volume data "
                'source. Install it with: pip install -e ".[databricks]"'
            ) from exc
        # Auth from the environment: app service principal (DATABRICKS_HOST +
        # DATABRICKS_CLIENT_ID/SECRET, injected by Databricks Apps) or a
        # user token locally (DATABRICKS_HOST + DATABRICKS_TOKEN).
        return WorkspaceClient().files

    def _download(self, filename: str, destination: Path) -> None:
        source = f"{self._volume_dir}/processed/{filename}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        response = self._files_client().download(source)
        tmp = destination.with_suffix(destination.suffix + ".tmp")
        with open(tmp, "wb") as fh:
            fh.write(response.contents.read())
        tmp.replace(destination)

    def _require(self, path: Path, hint: str) -> None:
        if not path.exists() and path.name in self._FILES:
            try:
                self._download(path.name, path)
            except Exception as exc:
                raise MissingDataError(
                    f"Could not fetch {path.name} from {self._volume_dir}/processed via the Files API: {exc}"
                ) from exc
        super()._require(path, hint)


def get_data_source() -> DataSource:
    """Build the configured data source.

    ``CAREGAP_DATA_SOURCE=local`` (default) reads CSVs/Parquet from
    ``CAREGAP_DATA_DIR`` - which may also point at a mounted Unity Catalog
    volume such as ``/Volumes/main/caregap/data``. ``databricks`` reads the
    registered tables through a SQL warehouse instead.
    """
    mode = os.environ.get("CAREGAP_DATA_SOURCE", "local").strip().lower()
    if mode == "databricks":
        return DatabricksDataSource()
    if mode == "volume":
        return VolumeDataSource()
    if mode == "local":
        return LocalDataSource()
    raise ValueError(f"Unknown CAREGAP_DATA_SOURCE: {mode!r} (expected 'local', 'volume' or 'databricks')")
