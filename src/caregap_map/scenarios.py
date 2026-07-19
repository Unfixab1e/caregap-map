"""Saved planning scenarios: a structured snapshot of a planner's selection.

A :class:`PlanningScenario` records what the planner was looking at (state/
district, capability), what the aggregate evidence said at that moment
(counts, judgeable %, trusted-record share, trust-weighted ICU evidence
index), an optional facility list and note, plus the scoring-config hash
and a data-snapshot identifier so a reopened scenario can be compared
against the data it was saved from.

Persistence mirrors reviewer notes (:mod:`caregap_map.persistence`):
SQLite locally, a Unity Catalog Delta table on Databricks
(``CAREGAP_SCENARIO_STORE=sqlite|databricks``, falling back to
``CAREGAP_REVIEW_STORE`` so both features use the same backend by
default). Scenario values never feed scoring or classification.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import pandas as pd
from pydantic import BaseModel, Field

from .config import ScoringConfig, default_paths
from .databricks_connect import connect_warehouse, have_warehouse_credentials, resolve_http_path

_IDENTIFIER = re.compile(r"^[A-Za-z0-9_]+$")

# Column order shared by both backends (and the SELECT statements).
_COLUMNS = (
    "id",
    "created_at",
    "author",
    "name",
    "capability",
    "state",
    "district",
    "region_status",
    "facility_count",
    "trusted_count",
    "needs_review_count",
    "no_icu_evidence_count",
    "insufficient_data_count",
    "judgeable_pct",
    "trusted_record_share_pct",
    "trust_weighted_evidence_index",
    "selected_facility_ids_json",
    "note",
    "scoring_config_hash",
    "data_snapshot",
)


class PlanningScenario(BaseModel):
    """One saved planning scenario. All aggregates are copies, not references."""

    id: str | None = None
    created_at: str = ""
    author: str = ""
    name: str
    capability: str = "ICU"
    state: str | None = None  # None = All India
    district: str | None = None
    region_status: str = ""
    facility_count: int = 0
    trusted_count: int = 0
    needs_review_count: int = 0
    no_icu_evidence_count: int = 0
    insufficient_data_count: int = 0
    judgeable_pct: float = 0.0
    trusted_record_share_pct: float = 0.0
    trust_weighted_evidence_index: float = 0.0
    selected_facility_ids: list[str] = Field(default_factory=list)
    note: str = ""
    scoring_config_hash: str = ""
    data_snapshot: str = ""

    @property
    def region_label(self) -> str:
        if not self.state:
            return "All India"
        return f"{self.state} / {self.district}" if self.district else self.state


def _validate(scenario: PlanningScenario) -> None:
    if not scenario.name.strip():
        raise ValueError("Refusing to store a scenario without a name.")


def _row_to_scenario(row: tuple) -> PlanningScenario:
    data = dict(zip(_COLUMNS, row, strict=True))
    data["selected_facility_ids"] = json.loads(data.pop("selected_facility_ids_json") or "[]")
    return PlanningScenario(**data)


def _scenario_to_params(scenario: PlanningScenario, scenario_id: str, created_at: str) -> dict:
    return {
        "id": scenario_id,
        "created_at": created_at,
        "author": scenario.author,
        "name": scenario.name.strip(),
        "capability": scenario.capability,
        "state": scenario.state,
        "district": scenario.district,
        "region_status": scenario.region_status,
        "facility_count": scenario.facility_count,
        "trusted_count": scenario.trusted_count,
        "needs_review_count": scenario.needs_review_count,
        "no_icu_evidence_count": scenario.no_icu_evidence_count,
        "insufficient_data_count": scenario.insufficient_data_count,
        "judgeable_pct": scenario.judgeable_pct,
        "trusted_record_share_pct": scenario.trusted_record_share_pct,
        "trust_weighted_evidence_index": scenario.trust_weighted_evidence_index,
        "selected_facility_ids_json": json.dumps(scenario.selected_facility_ids),
        "note": scenario.note,
        "scoring_config_hash": scenario.scoring_config_hash,
        "data_snapshot": scenario.data_snapshot,
    }


class ScenarioStore(Protocol):
    """Persistence contract for planning scenarios."""

    def save_scenario(self, scenario: PlanningScenario) -> PlanningScenario: ...

    def list_scenarios(self) -> list[PlanningScenario]: ...

    def get_scenario(self, scenario_id: str) -> PlanningScenario | None: ...

    def delete_scenario(self, scenario_id: str) -> bool: ...


class SqliteScenarioStore:
    """SQLite-backed scenario store; shares the local reviews.db file."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS planning_scenarios (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    author TEXT NOT NULL DEFAULT '',
                    name TEXT NOT NULL,
                    capability TEXT NOT NULL DEFAULT 'ICU',
                    state TEXT,
                    district TEXT,
                    region_status TEXT NOT NULL DEFAULT '',
                    facility_count INTEGER NOT NULL DEFAULT 0,
                    trusted_count INTEGER NOT NULL DEFAULT 0,
                    needs_review_count INTEGER NOT NULL DEFAULT 0,
                    no_icu_evidence_count INTEGER NOT NULL DEFAULT 0,
                    insufficient_data_count INTEGER NOT NULL DEFAULT 0,
                    judgeable_pct REAL NOT NULL DEFAULT 0,
                    trusted_record_share_pct REAL NOT NULL DEFAULT 0,
                    trust_weighted_evidence_index REAL NOT NULL DEFAULT 0,
                    selected_facility_ids_json TEXT NOT NULL DEFAULT '[]',
                    note TEXT NOT NULL DEFAULT '',
                    scoring_config_hash TEXT NOT NULL DEFAULT '',
                    data_snapshot TEXT NOT NULL DEFAULT ''
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def save_scenario(self, scenario: PlanningScenario) -> PlanningScenario:
        _validate(scenario)
        scenario_id = uuid.uuid4().hex
        created_at = scenario.created_at or datetime.now(UTC).isoformat(timespec="seconds")
        params = _scenario_to_params(scenario, scenario_id, created_at)
        placeholders = ", ".join(f":{c}" for c in _COLUMNS)
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO planning_scenarios ({', '.join(_COLUMNS)}) VALUES ({placeholders})",
                params,
            )
        return scenario.model_copy(update={"id": scenario_id, "created_at": created_at})

    def list_scenarios(self) -> list[PlanningScenario]:
        query = (
            f"SELECT {', '.join(_COLUMNS)} FROM planning_scenarios ORDER BY created_at DESC, id DESC"
        )
        with self._connect() as conn:
            rows = conn.execute(query).fetchall()
        return [_row_to_scenario(r) for r in rows]

    def get_scenario(self, scenario_id: str) -> PlanningScenario | None:
        query = f"SELECT {', '.join(_COLUMNS)} FROM planning_scenarios WHERE id = ?"
        with self._connect() as conn:
            row = conn.execute(query, (scenario_id,)).fetchone()
        return _row_to_scenario(row) if row else None

    def delete_scenario(self, scenario_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM planning_scenarios WHERE id = ?", (scenario_id,))
        return cur.rowcount > 0


class DeltaScenarioStore:
    """Planning scenarios in a Unity Catalog Delta table via a SQL warehouse.

    Same conventions as :class:`~caregap_map.persistence.DeltaReviewStore`:
    native parameterized queries only (user text never lands in SQL
    strings), create-on-first-use with a read-probe fallback, injectable
    ``connection_factory`` for tests.
    """

    def __init__(
        self,
        catalog: str | None = None,
        schema: str | None = None,
        table: str = "planning_scenarios",
        host: str | None = None,
        http_path: str | None = None,
        token: str | None = None,
        connection_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.catalog = catalog or os.environ.get("CAREGAP_DATABRICKS_CATALOG", "main")
        self.schema = schema or os.environ.get("CAREGAP_DATABRICKS_SCHEMA", "caregap")
        self.table = table
        for name, value in (
            ("catalog", self.catalog),
            ("schema", self.schema),
            ("table", self.table),
        ):
            if not _IDENTIFIER.match(value):
                raise ValueError(f"Invalid Databricks {name} identifier: {value!r}")
        self._host = host or os.environ.get("DATABRICKS_HOST", "")
        self._http_path = resolve_http_path(http_path)
        self._token = token or os.environ.get("DATABRICKS_TOKEN", "")
        self._connection_factory = connection_factory
        if connection_factory is None and not (
            self._http_path and have_warehouse_credentials(self._host, self._token)
        ):
            raise RuntimeError(
                "DeltaScenarioStore needs DATABRICKS_HOST plus a warehouse HTTP path "
                "(DATABRICKS_HTTP_PATH or DATABRICKS_WAREHOUSE_ID) and either "
                "DATABRICKS_TOKEN or app service-principal OAuth (see DEPLOYMENT.md), "
                "or CAREGAP_SCENARIO_STORE=sqlite."
            )
        self._ensure_table()

    @property
    def _qualified(self) -> str:
        return f"`{self.catalog}`.`{self.schema}`.`{self.table}`"

    def _connect(self):
        if self._connection_factory is not None:
            return self._connection_factory()
        return connect_warehouse(self._host, self._http_path, self._token)

    def _execute(self, sql: str, parameters: dict | None = None, fetch: bool = False):
        with self._connect() as conn, conn.cursor() as cursor:
            cursor.execute(sql, parameters or {})
            return cursor.fetchall() if fetch else None

    def _ensure_table(self) -> None:
        try:
            self._execute(
                f"CREATE TABLE IF NOT EXISTS {self._qualified} ("
                "id STRING, created_at STRING, author STRING, name STRING, "
                "capability STRING, state STRING, district STRING, region_status STRING, "
                "facility_count INT, trusted_count INT, needs_review_count INT, "
                "no_icu_evidence_count INT, insufficient_data_count INT, "
                "judgeable_pct DOUBLE, trusted_record_share_pct DOUBLE, "
                "trust_weighted_evidence_index DOUBLE, selected_facility_ids_json STRING, "
                "note STRING, scoring_config_hash STRING, data_snapshot STRING)"
            )
        except Exception:
            # The app service principal may lack CREATE while the table
            # already exists; accept that state if it is queryable.
            self._execute(f"SELECT 1 FROM {self._qualified} LIMIT 1", fetch=True)

    def save_scenario(self, scenario: PlanningScenario) -> PlanningScenario:
        _validate(scenario)
        scenario_id = uuid.uuid4().hex
        created_at = scenario.created_at or datetime.now(UTC).isoformat(timespec="seconds")
        params = _scenario_to_params(scenario, scenario_id, created_at)
        placeholders = ", ".join(f":{c}" for c in _COLUMNS)
        self._execute(
            f"INSERT INTO {self._qualified} ({', '.join(_COLUMNS)}) VALUES ({placeholders})",
            params,
        )
        return scenario.model_copy(update={"id": scenario_id, "created_at": created_at})

    def list_scenarios(self) -> list[PlanningScenario]:
        rows = (
            self._execute(
                f"SELECT {', '.join(_COLUMNS)} FROM {self._qualified} "
                "ORDER BY created_at DESC, id DESC",
                fetch=True,
            )
            or []
        )
        return [_row_to_scenario(tuple(r)) for r in rows]

    def get_scenario(self, scenario_id: str) -> PlanningScenario | None:
        rows = (
            self._execute(
                f"SELECT {', '.join(_COLUMNS)} FROM {self._qualified} WHERE id = :id",
                {"id": scenario_id},
                fetch=True,
            )
            or []
        )
        return _row_to_scenario(tuple(rows[0])) if rows else None

    def delete_scenario(self, scenario_id: str) -> bool:
        existing = self.get_scenario(scenario_id)
        if existing is None:
            return False
        self._execute(f"DELETE FROM {self._qualified} WHERE id = :id", {"id": scenario_id})
        return True


def get_scenario_store() -> ScenarioStore:
    """Build the configured scenario store.

    ``CAREGAP_SCENARIO_STORE`` selects the backend; when unset it follows
    ``CAREGAP_REVIEW_STORE`` (so notes and scenarios share a backend),
    defaulting to SQLite.
    """
    mode = (
        os.environ.get("CAREGAP_SCENARIO_STORE")
        or os.environ.get("CAREGAP_REVIEW_STORE", "sqlite")
    ).strip().lower()
    if mode == "databricks":
        return DeltaScenarioStore()
    if mode == "sqlite":
        return SqliteScenarioStore(default_paths().reviews_db)
    raise ValueError(f"Unknown CAREGAP_SCENARIO_STORE: {mode!r} (expected 'sqlite' or 'databricks')")


# ---------------------------------------------------------------------------
# Snapshot helpers (used by the app when building a scenario)
# ---------------------------------------------------------------------------


def scoring_config_fingerprint(config: ScoringConfig) -> str:
    """Short stable hash of every active weight/threshold/keyword."""
    return hashlib.sha256(config.model_dump_json().encode("utf-8")).hexdigest()[:12]


def data_snapshot_id(scored: pd.DataFrame) -> str:
    """Identifier for the loaded dataset: row count + classification digest."""
    counts = scored["classification"].value_counts().sort_index()
    digest = hashlib.sha256(counts.to_json().encode("utf-8")).hexdigest()[:12]
    return f"{len(scored)} records / {digest}"


def scenario_from_summary(
    *,
    name: str,
    summary: dict,
    state: str | None,
    district: str | None,
    author: str = "",
    note: str = "",
    capability: str = "ICU",
    selected_facility_ids: list[str] | None = None,
    scoring_config_hash: str = "",
    data_snapshot: str = "",
) -> PlanningScenario:
    """Build a scenario from a :func:`~caregap_map.aggregation.summarize_facilities` dict."""
    return PlanningScenario(
        name=name,
        author=author,
        note=note,
        capability=capability,
        state=state,
        district=district,
        region_status=summary.get("region_status", ""),
        facility_count=summary.get("facility_count", 0),
        trusted_count=summary.get("trusted_icu_count", 0),
        needs_review_count=summary.get("needs_review_count", 0),
        no_icu_evidence_count=summary.get("likely_gap_count", 0),
        insufficient_data_count=summary.get("insufficient_data_count", 0),
        judgeable_pct=summary.get("pct_sufficient_data", 0.0),
        trusted_record_share_pct=summary.get("evidence_coverage_pct", 0.0),
        trust_weighted_evidence_index=summary.get("trust_weighted_icu_coverage", 0.0),
        selected_facility_ids=selected_facility_ids or [],
        scoring_config_hash=scoring_config_hash,
        data_snapshot=data_snapshot,
    )
