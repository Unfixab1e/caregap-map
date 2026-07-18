"""Data source adapters.

The application talks to a :class:`DataSource` protocol, never to file
paths or Databricks APIs directly. The local adapter reads the raw CSVs
and processed Parquet files; a Databricks adapter can implement the same
protocol against Unity Catalog tables without touching scoring or UI code.
"""

from __future__ import annotations

from typing import Protocol

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
        self._require(
            self.paths.pin_directory_csv, "Place india_post_pincode_directory.csv under data/raw/."
        )
        return pd.read_csv(self.paths.pin_directory_csv, dtype=str)

    def load_nfhs_raw(self) -> pd.DataFrame:
        self._require(
            self.paths.nfhs_csv, "Place nfhs_5_district_health_indicators.csv under data/raw/."
        )
        return pd.read_csv(self.paths.nfhs_csv, dtype=str)

    def load_scored_facilities(self) -> pd.DataFrame:
        self._require(
            self.paths.facilities_scored_parquet,
            "Run `python scripts/build_processed_data.py` first.",
        )
        return pd.read_parquet(self.paths.facilities_scored_parquet)

    def load_region_summary(self, level: str) -> pd.DataFrame:
        path = (
            self.paths.region_state_parquet if level == "state" else self.paths.region_district_parquet
        )
        self._require(path, "Run `python scripts/build_processed_data.py` first.")
        return pd.read_parquet(path)


class DatabricksDataSource:
    """Placeholder adapter for the Databricks deployment milestone.

    Will read the same logical tables from Unity Catalog (e.g. via
    ``databricks-sql-connector`` or Spark) and expose them through the
    identical :class:`DataSource` protocol. Not used in the local milestone.
    """

    def __init__(self, catalog: str, schema: str) -> None:
        self.catalog = catalog
        self.schema = schema

    def __getattr__(self, name: str):
        raise NotImplementedError(
            "DatabricksDataSource is a stub for the deployment milestone; "
            "use LocalDataSource for local development."
        )
