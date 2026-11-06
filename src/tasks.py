"""Durable tasks shared by IRA and the desktop bridge.

Dates require an ISO-8601 timestamp with an explicit timezone (``Z`` or
``+/-HH:MM``). Never guess a timezone for a spoken date: callers resolve it
before writing. Stored timestamps always use UTC and a trailing ``Z``.

SQLite calls block, so async consumers use ``asyncio.to_thread``. An instance
serializes its own connection across those worker threads; WAL and a busy
timeout coordinate with the other process using the same database.
"""

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock


def default_tasks_db_path() -> Path:
    """Where the task store lives."""
    override = os.environ.get("IRA_TASKS_DB") or os.environ.get("IRA_MEMORY_DB")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / "ira_tasks.db"


MAX_TITLE_LENGTH = 500
MAX_TASK_LIMIT = 100
MAX_TASK_ID = 2**63 - 1

_DUE_DATE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?"
    r"(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)"
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY,
    title       TEXT NOT NULL,
    due_at      TEXT NULL,
    done        INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS tasks_upcoming ON tasks(done, due_at, id);
"""


@dataclass(frozen=True)
class Task:
    id: int
    title: str
    due_at: str | None
    done: bool
    created_at: str


def _utc_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _due_at(value: str | None) -> str | None:
    if value is None:
        return None
    message = "due_at must be an ISO-8601 timestamp with a timezone (Z or +/-HH:MM)"
    if not isinstance(value, str) or _DUE_DATE.fullmatch(value) is None:
        raise ValueError(message)
    try:
        return _utc_timestamp(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except (ValueError, OverflowError) as exc:
        raise ValueError(message) from exc


def _task_id(value: int) -> None:
    if type(value) is not int or not 1 <= value <= MAX_TASK_ID:
        raise ValueError("task id must be a positive integer")


def _task(row: sqlite3.Row) -> Task:
    return Task(
        id=row["id"],
        title=row["title"],
        due_at=row["due_at"],
        done=bool(row["done"]),
        created_at=row["created_at"],
    )


class TaskStore:
    """A lazy SQLite task store, safe to call from concurrent worker threads."""

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path is not None else default_tasks_db_path()
        self._conn: sqlite3.Connection | None = None
        self._lock = RLock()

    def connect(self) -> sqlite3.Connection:
        """Open on first use; store methods hold the lock while using it."""
        with self._lock:
            if self._conn is not None:
                return self._conn
            if str(self._path) != ":memory:":
                self._path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self._path), check_same_thread=False)
            try:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA busy_timeout=5000")
                conn.execute("PRAGMA journal_mode=WAL")
                conn.executescript(_SCHEMA)
                conn.commit()
            except Exception:
                conn.close()
                raise
            self._conn = conn
            return conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def add(self, title: str, due_at: str | None = None) -> Task:
        """Add a task; reject empty/overlong titles and unresolved due dates."""
        if not isinstance(title, str):
            raise ValueError("title must be a string")
        title = " ".join(title.split())
        if not title or len(title) > MAX_TITLE_LENGTH:
            raise ValueError(f"title must contain 1 to {MAX_TITLE_LENGTH} characters")
        due_at = _due_at(due_at)
        created_at = _utc_timestamp(datetime.now(timezone.utc))
        with self._lock, self.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO tasks (title, due_at, created_at) VALUES (?, ?, ?)",
                (title, due_at, created_at),
            )
            return Task(cursor.lastrowid, title, due_at, False, created_at)

    def upcoming(self, limit: int = 8) -> list[Task]:
        """Return unfinished tasks, earliest due first and undated tasks last."""
        if type(limit) is not int or not 1 <= limit <= MAX_TASK_LIMIT:
            raise ValueError(f"limit must be an integer from 1 to {MAX_TASK_LIMIT}")
        with self._lock:
            rows = (
                self.connect()
                .execute(
                    "SELECT * FROM tasks WHERE done = 0"
                    # Removing Z makes 12:00:00 sort before 12:00:00.000001.
                    # All records are UTC, so this also preserves subsecond order.
                    " ORDER BY due_at IS NULL, rtrim(due_at, 'Z'), id LIMIT ?",
                    (limit,),
                )
                .fetchall()
            )
            return [_task(row) for row in rows]

    def set_done(self, task_id: int, done: bool) -> Task | None:
        """Toggle completion, returning None when the id does not exist."""
        _task_id(task_id)
        if type(done) is not bool:
            raise ValueError("done must be a boolean")
        with self._lock, self.connect() as conn:
            cursor = conn.execute(
                "UPDATE tasks SET done = ? WHERE id = ?", (int(done), task_id)
            )
            if cursor.rowcount == 0:
                return None
            return _task(
                conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            )

    def delete(self, task_id: int) -> bool:
        """Delete a task, returning whether a record was removed."""
        _task_id(task_id)
        with self._lock, self.connect() as conn:
            cursor = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            return cursor.rowcount > 0
