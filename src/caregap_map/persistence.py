"""Reviewer notes and planning scenarios.

The app depends on the :class:`ReviewStore` protocol only, obtained via
:func:`get_review_store` (``CAREGAP_REVIEW_STORE=sqlite|databricks``).
Local development uses SQLite; the deployed app uses a Unity Catalog
Delta table so notes survive app restarts and redeployments.
"""

from __future__ import annotations

import os
import re
import sqlite3
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel

from .config import default_paths

# What a note can be attached to.
SCOPE_TYPES = ("facility", "district", "state")

_IDENTIFIER = re.compile(r"^[A-Za-z0-9_]+$")


class ReviewNote(BaseModel):
    """A reviewer note attached to a facility, district or state."""

    id: int | str | None = None  # int for SQLite, UUID string for Delta
    created_at: str = ""
    scope_type: str
    scope_id: str  # facility unique_id, "State" or "State/District"
    note: str
    author: str = ""


def _validate_note(note: ReviewNote) -> None:
    if note.scope_type not in SCOPE_TYPES:
        raise ValueError(f"scope_type must be one of {SCOPE_TYPES}, got {note.scope_type!r}")
    if not note.note.strip():
        raise ValueError("Refusing to store an empty note.")


class ReviewStore(Protocol):
    """Persistence contract for reviewer notes."""

    def add_note(self, note: ReviewNote) -> ReviewNote: ...

    def list_notes(self, scope_type: str | None = None, scope_id: str | None = None) -> list[ReviewNote]: ...


class SqliteReviewStore:
    """SQLite-backed note store; one file, no server, safe for local use."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS review_notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    scope_type TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    note TEXT NOT NULL,
                    author TEXT NOT NULL DEFAULT ''
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def add_note(self, note: ReviewNote) -> ReviewNote:
        _validate_note(note)
        created_at = note.created_at or datetime.now(UTC).isoformat(timespec="seconds")
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO review_notes (created_at, scope_type, scope_id, note, author) "
                "VALUES (?, ?, ?, ?, ?)",
                (created_at, note.scope_type, note.scope_id, note.note.strip(), note.author),
            )
            note_id = cur.lastrowid
        return note.model_copy(update={"id": note_id, "created_at": created_at})

    def list_notes(self, scope_type: str | None = None, scope_id: str | None = None) -> list[ReviewNote]:
        query = "SELECT id, created_at, scope_type, scope_id, note, author FROM review_notes"
        clauses, params = [], []
        if scope_type is not None:
            clauses.append("scope_type = ?")
            params.append(scope_type)
        if scope_id is not None:
            clauses.append("scope_id = ?")
            params.append(scope_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC, id DESC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            ReviewNote(id=r[0], created_at=r[1], scope_type=r[2], scope_id=r[3], note=r[4], author=r[5])
            for r in rows
        ]


class DeltaReviewStore:
    """Reviewer notes in a Unity Catalog Delta table via a SQL warehouse.

    Survives app restarts and redeployments (the app filesystem is
    ephemeral). Uses databricks-sql-connector *native parameterized
    queries* - user text is never interpolated into SQL strings. The table
    is created on first use.

    Credentials follow the same environment variables as
    :class:`~caregap_map.data_access.DatabricksDataSource`;
    ``connection_factory`` is injectable for tests.
    """

    def __init__(
        self,
        catalog: str | None = None,
        schema: str | None = None,
        table: str = "review_notes",
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
        self._http_path = http_path or os.environ.get("DATABRICKS_HTTP_PATH", "")
        self._token = token or os.environ.get("DATABRICKS_TOKEN", "")
        self._connection_factory = connection_factory
        if connection_factory is None and not (self._host and self._http_path and self._token):
            raise RuntimeError(
                "DeltaReviewStore needs DATABRICKS_HOST, DATABRICKS_HTTP_PATH and "
                "DATABRICKS_TOKEN (see DEPLOYMENT.md), or CAREGAP_REVIEW_STORE=sqlite."
            )
        self._ensure_table()

    @property
    def _qualified(self) -> str:
        return f"`{self.catalog}`.`{self.schema}`.`{self.table}`"

    def _connect(self):
        if self._connection_factory is not None:
            return self._connection_factory()
        try:
            from databricks import sql as dbsql
        except ImportError as exc:
            raise ImportError(
                "The 'databricks-sql-connector' package is required for the Delta "
                'review store. Install it with: pip install -e ".[databricks]"'
            ) from exc
        return dbsql.connect(
            server_hostname=self._host.removeprefix("https://"),
            http_path=self._http_path,
            access_token=self._token,
        )

    def _execute(self, sql: str, parameters: dict | None = None, fetch: bool = False):
        with self._connect() as conn, conn.cursor() as cursor:
            cursor.execute(sql, parameters or {})
            return cursor.fetchall() if fetch else None

    def _ensure_table(self) -> None:
        self._execute(
            f"CREATE TABLE IF NOT EXISTS {self._qualified} ("
            "id STRING, created_at STRING, scope_type STRING, "
            "scope_id STRING, note STRING, author STRING)"
        )

    def add_note(self, note: ReviewNote) -> ReviewNote:
        _validate_note(note)
        created_at = note.created_at or datetime.now(UTC).isoformat(timespec="seconds")
        note_id = uuid.uuid4().hex
        self._execute(
            f"INSERT INTO {self._qualified} "
            "(id, created_at, scope_type, scope_id, note, author) "
            "VALUES (:id, :created_at, :scope_type, :scope_id, :note, :author)",
            {
                "id": note_id,
                "created_at": created_at,
                "scope_type": note.scope_type,
                "scope_id": note.scope_id,
                "note": note.note.strip(),
                "author": note.author,
            },
        )
        return note.model_copy(update={"id": note_id, "created_at": created_at})

    def list_notes(self, scope_type: str | None = None, scope_id: str | None = None) -> list[ReviewNote]:
        query = f"SELECT id, created_at, scope_type, scope_id, note, author FROM {self._qualified}"
        clauses, params = [], {}
        if scope_type is not None:
            clauses.append("scope_type = :scope_type")
            params["scope_type"] = scope_type
        if scope_id is not None:
            clauses.append("scope_id = :scope_id")
            params["scope_id"] = scope_id
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC, id DESC"
        rows = self._execute(query, params, fetch=True) or []
        return [
            ReviewNote(id=r[0], created_at=r[1], scope_type=r[2], scope_id=r[3], note=r[4], author=r[5])
            for r in rows
        ]


def get_review_store() -> ReviewStore:
    """Build the configured note store.

    ``CAREGAP_REVIEW_STORE=sqlite`` (default) stores notes in a local file;
    ``databricks`` uses the Unity Catalog Delta table so notes survive app
    restarts and redeployments.
    """
    mode = os.environ.get("CAREGAP_REVIEW_STORE", "sqlite").strip().lower()
    if mode == "databricks":
        return DeltaReviewStore()
    if mode == "sqlite":
        return SqliteReviewStore(default_paths().reviews_db)
    raise ValueError(f"Unknown CAREGAP_REVIEW_STORE: {mode!r} (expected 'sqlite' or 'databricks')")
