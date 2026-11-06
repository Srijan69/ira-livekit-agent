"""Durable, monotonic skill-run history shared with the read-only bridge."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psutil

DEFAULT_RUN_PATH = (
    Path(__file__).resolve().parent.parent / "workflows" / ".runs.sqlite3"
)
TERMINAL_STATUSES = frozenset({"done", "error", "cancelled", "uncertain"})


class SkillRunStore:
    """SQLite is the delivery ledger, including across process restarts.

    Construction never changes existing run outcomes. Only the agent owning
    execution calls :meth:`recover_interrupted`; a dashboard reader must not
    mark another process's live work interrupted.
    """

    def __init__(
        self, path: str | Path | None = None, *, recover_interrupted: bool = False
    ) -> None:
        self.path = Path(path) if path is not None else DEFAULT_RUN_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS skill_runs ("
                "run_id TEXT PRIMARY KEY, status TEXT NOT NULL, "
                "started_at REAL NOT NULL, record TEXT NOT NULL)"
            )
        if recover_interrupted:
            self.recover_interrupted()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def create(self, record: dict[str, Any]) -> bool:
        """Claim a delivery ID atomically; a duplicate may never execute."""
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO skill_runs VALUES (?, ?, ?, ?)",
                (
                    record["run_id"],
                    record["status"],
                    record["started_at"],
                    json.dumps(record, ensure_ascii=False),
                ),
            )
            return cursor.rowcount == 1

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT record FROM skill_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT record FROM skill_runs ORDER BY started_at DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def update(self, run_id: str, **fields: Any) -> dict[str, Any] | None:
        """Terminal outcomes are immutable, even when an action finishes late."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT record FROM skill_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                return None
            record = json.loads(row[0])
            if record["status"] in TERMINAL_STATUSES:
                return record
            # Identity and the frozen source are never replaceable by updates.
            for key in (
                "run_id",
                "skill_slug",
                "skill_name",
                "skill_version",
                "skill_markdown",
                "started_at",
            ):
                fields.pop(key, None)
            record.update(fields)
            if record["status"] in TERMINAL_STATUSES:
                record.update(question="", question_id="", input_name="")
            connection.execute(
                "UPDATE skill_runs SET status = ?, record = ? WHERE run_id = ?",
                (record["status"], json.dumps(record, ensure_ascii=False), run_id),
            )
            return record

    def record_step(self, run_id: str, step: dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT record FROM skill_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            record = json.loads(row[0])
            if record["status"] in TERMINAL_STATUSES:
                return
            steps = record.setdefault("steps", [])
            steps[:] = [
                item for item in steps if item["step_index"] != step["step_index"]
            ]
            steps.append(step)
            connection.execute(
                "UPDATE skill_runs SET record = ? WHERE run_id = ?",
                (json.dumps(record, ensure_ascii=False), run_id),
            )

    def recover_interrupted(self) -> int:
        """Mark unfinished prior deliveries uncertain, without replaying them."""
        count = 0
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT run_id, record FROM skill_runs"
            ).fetchall()
            for run_id, source in rows:
                record = json.loads(source)
                if record["status"] in TERMINAL_STATUSES:
                    continue
                pid = record.get("owner_pid")
                created_at = record.get("owner_created_at")
                if pid is not None and created_at is not None:
                    try:
                        owner = psutil.Process(pid)
                        if owner.is_running() and owner.create_time() == created_at:
                            continue
                    except psutil.AccessDenied:
                        # Lack of permission is not evidence the owner died.
                        continue
                    except psutil.NoSuchProcess:
                        pass
                record.update(
                    status="uncertain",
                    question="",
                    question_id="",
                    input_name="",
                    verified=False,
                    message="Execution interrupted; side effects require observation before retry.",
                    finished_at=time.time() * 1000,
                )
                connection.execute(
                    "UPDATE skill_runs SET status = ?, record = ? WHERE run_id = ?",
                    ("uncertain", json.dumps(record, ensure_ascii=False), run_id),
                )
                count += 1
        return count
