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
from typing import Any, Protocol

import pandas as pd

from .config import DataPaths, default_paths


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
        self._http_path = http_path or os.environ.get("DATABRICKS_HTTP_PATH", "")
        self._token = token or os.environ.get("DATABRICKS_TOKEN", "")
        self._connection_factory = connection_factory
        for name, value in (("catalog", self.catalog), ("schema", self.schema)):
            if not _IDENTIFIER.match(value):
                raise ValueError(f"Invalid Databricks {name} identifier: {value!r}")
        if connection_factory is None and not (self._host and self._http_path and self._token):
            raise MissingDataError(
                "DatabricksDataSource needs DATABRICKS_HOST, DATABRICKS_HTTP_PATH and "
                "DATABRICKS_TOKEN (see DEPLOYMENT.md)."
            )

    def _connect(self):
        if self._connection_factory is not None:
            return self._connection_factory()
        try:
            from databricks import sql as dbsql
        except ImportError as exc:
            raise ImportError(
                "The 'databricks-sql-connector' package is required for the Databricks "
                'data source. Install it with: pip install -e ".[databricks]"'
            ) from exc
        return dbsql.connect(
            server_hostname=self._host.removeprefix("https://"),
            http_path=self._http_path,
            access_token=self._token,
        )

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
    if mode == "local":
        return LocalDataSource()
    raise ValueError(f"Unknown CAREGAP_DATA_SOURCE: {mode!r} (expected 'local' or 'databricks')")
