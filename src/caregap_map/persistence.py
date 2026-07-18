"""Reviewer notes and planning scenarios.

The app depends on the :class:`ReviewStore` protocol only. The local
implementation is SQLite (stdlib, zero setup); a Databricks-backed store
(Delta table / Lakebase) can implement the same protocol later.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

# What a note can be attached to.
SCOPE_TYPES = ("facility", "district", "state")


class ReviewNote(BaseModel):
    """A reviewer note attached to a facility, district or state."""

    id: int | None = None
    created_at: str = ""
    scope_type: str
    scope_id: str  # facility unique_id, "State" or "State/District"
    note: str
    author: str = ""


class ReviewStore(Protocol):
    """Persistence contract for reviewer notes."""

    def add_note(self, note: ReviewNote) -> ReviewNote: ...

    def list_notes(
        self, scope_type: str | None = None, scope_id: str | None = None
    ) -> list[ReviewNote]: ...


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
        if note.scope_type not in SCOPE_TYPES:
            raise ValueError(f"scope_type must be one of {SCOPE_TYPES}, got {note.scope_type!r}")
        if not note.note.strip():
            raise ValueError("Refusing to store an empty note.")
        created_at = note.created_at or datetime.now(UTC).isoformat(timespec="seconds")
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO review_notes (created_at, scope_type, scope_id, note, author) "
                "VALUES (?, ?, ?, ?, ?)",
                (created_at, note.scope_type, note.scope_id, note.note.strip(), note.author),
            )
            note_id = cur.lastrowid
        return note.model_copy(update={"id": note_id, "created_at": created_at})

    def list_notes(
        self, scope_type: str | None = None, scope_id: str | None = None
    ) -> list[ReviewNote]:
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
            ReviewNote(
                id=r[0], created_at=r[1], scope_type=r[2], scope_id=r[3], note=r[4], author=r[5]
            )
            for r in rows
        ]
