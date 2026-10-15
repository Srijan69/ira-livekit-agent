"""Durable memory for IRA: a small SQLite store she can write to and search.

The design constraint that shapes everything here is in ROADMAP.md: *do not
load everything every session*. A voice agent pays for its context twice --
once in tokens on every single turn, and once in latency, because a larger
prompt is a slower first token. So this module keeps two paths deliberately
separate:

  - `seed()` runs once at session start and returns only a handful of facts,
    chosen by how recently and how often they have actually been useful.
  - `recall()` runs on demand and searches the whole store.

That split is the difference between memory that scales and a system prompt
that grows until every reply is slow.

Search is FTS5 with BM25 ranking where the interpreter provides it, and a LIKE
scan where it does not -- some Python builds ship an SQLite without FTS5, and a
memory that silently stops finding things is worse than a slower one.

Every function here blocks: `sqlite3` is synchronous. Callers in `agent.py`
wrap each one in `asyncio.to_thread` for the same reason the browser tools do.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("agent.memory")

# How many facts to put in front of the model at session start. Small on
# purpose -- see the module docstring. Everything else is a `recall` away.
SEED_LIMIT = 12

# Default number of search hits. More than a few is unspeakable out loud.
RECALL_LIMIT = 5

# Longest fact accepted. Facts arrive via speech through the model, so a very
# long one means the transcript ran on, not that the boss said something
# unusually detailed.
MAX_FACT_LENGTH = 500

# Recognized categories. Free-form strings would fragment into "preference",
# "preferences" and "pref" within a week, and none of them would match.
KINDS = ("fact", "preference", "person", "project", "event")
DEFAULT_KIND = "fact"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id          INTEGER PRIMARY KEY,
    content     TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'fact',
    created_at  REAL NOT NULL,
    last_used_at REAL NOT NULL,
    use_count   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS memories_kind ON memories(kind);
CREATE INDEX IF NOT EXISTS memories_recency ON memories(last_used_at DESC);
"""

# FTS5 mirrors `memories` through triggers rather than storing the text twice.
_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content,
    content='memories',
    content_rowid='id',
    tokenize='porter unicode61'
);
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content)
    VALUES ('delete', old.id, old.content);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content)
    VALUES ('delete', old.id, old.content);
    INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
END;
"""


def default_db_path() -> Path:
    """Where the store lives. Overridable for tests and for a second profile."""
    override = os.environ.get("IRA_MEMORY_DB")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / "ira_memory.db"


@dataclass(frozen=True)
class Memory:
    id: int
    content: str
    kind: str
    created_at: float
    use_count: int


class MemoryStore:
    """A tiny durable store of things the boss told IRA to remember."""

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path is not None else default_db_path()
        self._conn: sqlite3.Connection | None = None
        self._fts = False

    # -- lifecycle ---------------------------------------------------------

    def connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn

        if str(self._path) != ":memory:":
            self._path.parent.mkdir(parents=True, exist_ok=True)

        # check_same_thread=False because every call arrives on whatever worker
        # thread `asyncio.to_thread` happened to pick. Access is still
        # serialized -- one tool call runs at a time.
        conn = sqlite3.connect(str(self._path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # WAL keeps a read during a write from blocking, which matters because
        # a blocked read here is silence on a voice call.
        # Suppressed rather than required: `:memory:` rejects WAL, and a store
        # that works without it is better than one that refuses to open.
        with contextlib.suppress(sqlite3.DatabaseError):
            conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)

        try:
            conn.executescript(_FTS_SCHEMA)
            self._fts = True
        except sqlite3.OperationalError:
            # No FTS5 in this interpreter's SQLite. Ranked search degrades to a
            # LIKE scan, which is slower and dumber but never silently empty.
            logger.warning("SQLite has no FTS5; memory search falls back to LIKE")
            self._fts = False

        conn.commit()
        self._conn = conn
        return conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # -- writing -----------------------------------------------------------

    def remember(self, content: str, *, kind: str = DEFAULT_KIND) -> str:
        """Store one fact. Returns a sentence IRA can say."""
        text = " ".join((content or "").split())[:MAX_FACT_LENGTH]
        if not text:
            return "There was nothing to remember."
        if kind not in KINDS:
            kind = DEFAULT_KIND

        conn = self.connect()
        now = time.time()

        # Re-saying something already known should not create a second row.
        # Exact-match dedupe only: deciding that two differently-worded facts
        # are the same is a judgement call, and getting it wrong silently drops
        # something the boss asked to keep.
        existing = conn.execute(
            "SELECT id FROM memories WHERE content = ? COLLATE NOCASE", (text,)
        ).fetchone()
        if existing is not None:
            conn.execute(
                "UPDATE memories SET last_used_at = ?, use_count = use_count + 1"
                " WHERE id = ?",
                (now, existing["id"]),
            )
            conn.commit()
            return "Already had that one, boss."

        conn.execute(
            "INSERT INTO memories (content, kind, created_at, last_used_at, use_count)"
            " VALUES (?, ?, ?, ?, 0)",
            (text, kind, now, now),
        )
        conn.commit()
        return "Noted, boss."

    def forget(self, query: str) -> str:
        """Delete the single best match for `query`."""
        hits = self.recall(query, limit=1, touch=False)
        if not hits:
            return "Nothing on file matches that."
        conn = self.connect()
        conn.execute("DELETE FROM memories WHERE id = ?", (hits[0].id,))
        conn.commit()
        return f"Forgotten: {hits[0].content}"

    # -- reading -----------------------------------------------------------

    def recall(
        self, query: str, *, limit: int = RECALL_LIMIT, touch: bool = True
    ) -> list[Memory]:
        """Search stored facts, best match first.

        `touch` records that a memory proved useful, which is what later feeds
        `seed()`. It is off for internal lookups like `forget`, so that
        deleting something does not first promote it.
        """
        text = " ".join((query or "").split())
        if not text:
            return []

        conn = self.connect()
        if self._fts:
            try:
                rows = conn.execute(
                    "SELECT m.* FROM memories_fts f"
                    " JOIN memories m ON m.id = f.rowid"
                    " WHERE memories_fts MATCH ?"
                    " ORDER BY bm25(memories_fts), m.use_count DESC"
                    " LIMIT ?",
                    (_fts_query(text), limit),
                ).fetchall()
            except sqlite3.OperationalError:
                # A query FTS5 cannot parse should not surface to the boss as a
                # crash; fall through to the scan.
                rows = _like_search(conn, text, limit)
        else:
            rows = _like_search(conn, text, limit)

        found = [
            Memory(
                id=r["id"],
                content=r["content"],
                kind=r["kind"],
                created_at=r["created_at"],
                use_count=r["use_count"],
            )
            for r in rows
        ]
        if touch and found:
            now = time.time()
            conn.executemany(
                "UPDATE memories SET last_used_at = ?, use_count = use_count + 1"
                " WHERE id = ?",
                [(now, m.id) for m in found],
            )
            conn.commit()
        return found

    def seed(self, limit: int = SEED_LIMIT) -> list[Memory]:
        """The few facts worth having in context before the boss says anything.

        Ranked by usefulness rather than recency alone: something recalled ten
        times over a month belongs in front of the model more than something
        saved yesterday and never referenced. Preferences outrank plain facts,
        since they change how IRA should answer rather than what she knows.
        """
        conn = self.connect()
        rows = conn.execute(
            "SELECT * FROM memories"
            " ORDER BY (kind = 'preference') DESC, use_count DESC, last_used_at DESC"
            " LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            Memory(
                id=r["id"],
                content=r["content"],
                kind=r["kind"],
                created_at=r["created_at"],
                use_count=r["use_count"],
            )
            for r in rows
        ]

    def count(self) -> int:
        return int(
            self.connect().execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        )

    def recent(self, limit: int = 5) -> list[Memory]:
        """Return the newest durable notes, bounded for startup context."""
        rows = (
            self.connect()
            .execute(
                "SELECT * FROM memories ORDER BY created_at DESC LIMIT ?", (limit,)
            )
            .fetchall()
        )
        return [
            Memory(
                id=row["id"],
                content=row["content"],
                kind=row["kind"],
                created_at=row["created_at"],
                use_count=row["use_count"],
            )
            for row in rows
        ]

    def preferences(self, limit: int = 12) -> list[Memory]:
        """Return stable user preferences for the startup persona context."""
        rows = (
            self.connect()
            .execute(
                "SELECT * FROM memories WHERE kind = 'preference' "
                "ORDER BY use_count DESC, last_used_at DESC LIMIT ?",
                (limit,),
            )
            .fetchall()
        )
        return [
            Memory(
                id=row["id"],
                content=row["content"],
                kind=row["kind"],
                created_at=row["created_at"],
                use_count=row["use_count"],
            )
            for row in rows
        ]


def _fts_query(text: str) -> str:
    """Turn spoken words into an FTS5 query.

    Each word is quoted and OR-ed. Quoting matters: FTS5 treats bare `AND`,
    `OR`, `NEAR` and punctuation as syntax, so an unquoted "coffee or tea"
    raises rather than searching. OR rather than AND because recall should
    surface near-misses -- the boss asking "what do I like to drink" should
    still find a fact phrased about coffee.
    """
    words = [w for w in (text or "").replace('"', " ").split() if w]
    return " OR ".join(f'"{w}"' for w in words)


def _like_search(conn: sqlite3.Connection, text: str, limit: int) -> list[sqlite3.Row]:
    """Fallback search for interpreters without FTS5."""
    words = [w for w in text.split() if len(w) > 2] or [text]
    clause = " OR ".join("content LIKE ?" for _ in words)
    params: list[object] = [f"%{w}%" for w in words]
    params.append(limit)
    # The f-string below interpolates `clause`, which is built above from a
    # fixed template repeated once per word -- never from the search text. The
    # words themselves go in as bound parameters.
    return conn.execute(
        f"SELECT * FROM memories WHERE {clause}"
        " ORDER BY use_count DESC, last_used_at DESC LIMIT ?",
        params,
    ).fetchall()
