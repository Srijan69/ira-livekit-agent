"""Transactional, bounded personal memory shared by the agent and bridge.

The journal never grants action authority. Source exclusions and content
tombstones prevent deleted material from returning through a late summary or a
legacy-memory adapter. Async consumers run these blocking methods in to_thread.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from uuid import uuid4
from weakref import WeakValueDictionary

import psutil


class MemoryConflict(ValueError):  # noqa: N818 - public API in the approved contract
    """A caller tried to change a revision which has already advanced."""


DEFAULT_SETTINGS = {
    "enabled": True,
    "context_budget": 16000,
    "summary_trigger": 12000,
    "recent_turns": 12,
    "summarizer_model": "gemini-3.8-flash",
    "revision": 0,
}
PROFILE_FIELDS = {"name": 120, "occupation": 200, "preferences": 4000}
SUMMARY_SECTIONS = ("facts", "decisions", "open_threads", "outcomes", "questions")
MAX_JOURNAL_CHARS = 24000
MAX_SUMMARY_CHARS = 24000
MAX_FACT_CHARS = 4000
_TERMINAL = {
    "completed",
    "succeeded",
    "success",
    "failed",
    "error",
    "interrupted",
    "cancelled",
    "uncertain",
    "canceled",
    "timeout",
    "timed_out",
    "verified",
}
_SUCCESS = {"completed", "succeeded", "success", "verified"}
_TOOL_KINDS = {
    "tool",
    "tool_call",
    "tool_result",
    "function_call",
    "function_call_output",
}
_MATCH_FOLD = str.maketrans(
    {
        **dict.fromkeys("\"'`\u2018\u2019\u201a\u201b\u201c\u201d\u201e\u201f", "'"),
        **dict.fromkeys("\u2010\u2011\u2012\u2013\u2014\u2015\u2212", "-"),
    }
)
_ACTIVE_STATUS_OWNERS = WeakValueDictionary()
_PROCESS_STARTED_AT = psutil.Process().create_time()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
INSERT OR IGNORE INTO metadata VALUES ('memory_revision', 0);
CREATE TABLE IF NOT EXISTS profile (
    id INTEGER PRIMARY KEY CHECK(id=1), name TEXT NOT NULL DEFAULT '', age INTEGER,
    occupation TEXT NOT NULL DEFAULT '', preferences TEXT NOT NULL DEFAULT '',
    revision INTEGER NOT NULL DEFAULT 0, updated_at TEXT, source TEXT NOT NULL DEFAULT 'settings'
);
INSERT OR IGNORE INTO profile(id) VALUES(1);
CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY, started_at TEXT NOT NULL, ended_at TEXT,
    checkpoint_revision INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS entries (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, content TEXT NOT NULL,
    normalized TEXT NOT NULL, created_at TEXT NOT NULL, source TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS entries_recent ON entries(created_at DESC, id);
CREATE VIRTUAL TABLE IF NOT EXISTS memory_search USING fts5(id UNINDEXED, content, tokenize='unicode61');
CREATE TRIGGER IF NOT EXISTS entry_insert AFTER INSERT ON entries BEGIN
    INSERT INTO memory_search(id,content) VALUES(new.id,new.content);
END;
CREATE TRIGGER IF NOT EXISTS entry_delete AFTER DELETE ON entries BEGIN
    DELETE FROM memory_search WHERE id=old.id;
END;
CREATE TABLE IF NOT EXISTS journal (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL,
    item_id TEXT NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL, kind TEXT NOT NULL,
    trusted INTEGER NOT NULL, tool_call_id TEXT, tool_name TEXT, status TEXT,
    created_at TEXT NOT NULL, excluded INTEGER NOT NULL DEFAULT 0,
    UNIQUE(conversation_id,item_id)
);
CREATE INDEX IF NOT EXISTS journal_prefix ON journal(conversation_id,excluded,seq);
CREATE INDEX IF NOT EXISTS journal_source ON journal(item_id);
CREATE TABLE IF NOT EXISTS checkpoints (
    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, through_seq INTEGER NOT NULL,
    summary TEXT NOT NULL, memory_revision INTEGER NOT NULL, created_at TEXT NOT NULL,
    invalidated INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS checkpoint_latest ON checkpoints(conversation_id,invalidated,through_seq DESC);
CREATE TABLE IF NOT EXISTS checkpoint_sources (
    checkpoint_id TEXT NOT NULL, item_id TEXT NOT NULL,
    PRIMARY KEY(checkpoint_id,item_id)
);
CREATE INDEX IF NOT EXISTS checkpoint_source ON checkpoint_sources(item_id,checkpoint_id);
CREATE TABLE IF NOT EXISTS facts (
    id TEXT PRIMARY KEY, target TEXT NOT NULL, normalized TEXT NOT NULL,
    revision INTEGER NOT NULL, deleted INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS fact_normalized ON facts(normalized,deleted);
CREATE TABLE IF NOT EXISTS fact_sources (
    fact_id TEXT NOT NULL, item_id TEXT NOT NULL, PRIMARY KEY(fact_id,item_id)
);
CREATE INDEX IF NOT EXISTS fact_source ON fact_sources(item_id,fact_id);
CREATE TABLE IF NOT EXISTS exclusions (item_id TEXT PRIMARY KEY, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tombstones (normalized TEXT PRIMARY KEY, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS explicit_overrides (normalized TEXT PRIMARY KEY, created_at TEXT NOT NULL);
PRAGMA user_version=1;
"""


def default_personal_memory_path() -> Path:
    override = os.environ.get("IRA_PERSONAL_MEMORY_DB")
    if override:
        return Path(override)
    directory = os.environ.get("IRA_MEMORIES_DIR")
    return (
        Path(directory)
        if directory
        else Path(__file__).resolve().parent.parent / "memories"
    ) / "personal.sqlite3"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalized(content: str) -> str:
    return " ".join(
        unicodedata.normalize("NFKC", content).translate(_MATCH_FOLD).casefold().split()
    )


def _string(value, field, limit, *, empty=False):
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise ValueError(f"{field} must be a string of at most {limit} characters")
    value = value.strip()
    if not empty and not value:
        raise ValueError(f"{field} must not be blank")
    return value


def _integer(value, field, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{field} must be an integer between {minimum} and {maximum}")
    return value


def _revision(value):
    if value is not None:
        _integer(value, "expected_revision", 0, 2**63 - 1)


def _sources(value):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 1000:
        raise ValueError("source_item_ids must be a bounded list")
    return list(dict.fromkeys(_string(item, "source item ID", 300) for item in value))


def _summary_text(summary):
    blocks = [summary["overview"]]
    for section in SUMMARY_SECTIONS:
        if summary[section]:
            blocks.append(
                section.replace("_", " ")
                + ":\n"
                + "\n".join(entry["content"] for entry in summary[section])
            )
    return "\n\n".join(blocks)


def _checkpoint(row):
    if row is None:
        return None
    return {
        "id": row["id"],
        "conversation_id": row["conversation_id"],
        "through_seq": row["through_seq"],
        "summary": json.loads(row["summary"]),
        "memory_revision": row["memory_revision"],
        "created_at": row["created_at"],
    }


def _journal(row):
    return {
        "seq": row["seq"],
        "item_id": row["item_id"],
        "role": row["role"],
        "text": row["text"],
        "kind": row["kind"],
        "trusted": bool(row["trusted"]),
        "tool_call_id": row["tool_call_id"],
        "tool_name": row["tool_name"],
        "status": row["status"],
    }


def _entry(row):
    return {key: row[key] for key in ("id", "kind", "content", "created_at", "source")}


class PersonalMemoryStore:
    """A lazy SQLite store with transactional cross-process revision checks."""

    def __init__(self, path=None):
        self._path = Path(path) if path is not None else default_personal_memory_path()
        self._conn = None
        self._lock = RLock()
        self._live_status = {}
        self._status_owner = str(uuid4())
        self._owned_conversations = set()

    def _connect(self):
        if self._conn is not None:
            return self._conn
        if str(self._path) != ":memory:":
            self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._path), check_same_thread=False, timeout=5)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA journal_mode=WAL")
            if conn.execute("PRAGMA user_version").fetchone()[0] > 1:
                raise ValueError(
                    "Personal memory schema is newer than this application"
                )
            conn.executescript(_SCHEMA)
            conn.execute(
                "INSERT OR IGNORE INTO settings VALUES (1,?)",
                (json.dumps(DEFAULT_SETTINGS),),
            )
            conn.commit()
        except BaseException:
            conn.close()
            raise
        self._conn = conn
        return conn

    @contextmanager
    def _transaction(self):
        with self._lock:
            conn = self._connect()
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    @staticmethod
    def _memory_revision(conn):
        return conn.execute(
            "SELECT value FROM metadata WHERE key='memory_revision'"
        ).fetchone()[0]

    @staticmethod
    def _advance(conn):
        conn.execute("UPDATE metadata SET value=value+1 WHERE key='memory_revision'")
        return PersonalMemoryStore._memory_revision(conn)

    @staticmethod
    def _profile(conn):
        row = conn.execute("SELECT * FROM profile WHERE id=1").fetchone()
        return {
            key: row[key]
            for key in (
                "name",
                "age",
                "occupation",
                "preferences",
                "revision",
                "updated_at",
            )
        }

    @staticmethod
    def _settings(conn):
        return json.loads(
            conn.execute("SELECT value FROM settings WHERE id=1").fetchone()[0]
        )

    @staticmethod
    def _excluded(conn, content):
        normalized = _normalized(content)
        if not normalized:
            return False
        if conn.execute(
            "SELECT 1 FROM explicit_overrides o JOIN entries e ON e.normalized=o.normalized WHERE o.normalized=? AND e.kind='fact' LIMIT 1",
            (normalized,),
        ).fetchone():
            return False
        return (
            conn.execute(
                "SELECT 1 FROM tombstones WHERE instr(?,normalized)>0 OR instr(normalized,?)>0 LIMIT 1",
                (normalized, normalized),
            ).fetchone()
            is not None
        )

    @staticmethod
    def _put_entry(conn, memory_id, kind, content, source):
        conn.execute(
            "INSERT INTO entries VALUES(?,?,?,?,?,?)",
            (memory_id, kind, content, _normalized(content), _now(), source),
        )

    @staticmethod
    def _invalidate_checkpoints(conn, ids=None):
        if ids is None:
            rows = conn.execute(
                "SELECT id FROM checkpoints WHERE invalidated=0"
            ).fetchall()
        else:
            rows = []
            for item_id in ids:
                rows.extend(
                    conn.execute(
                        "SELECT c.id FROM checkpoints c JOIN checkpoint_sources s ON c.id=s.checkpoint_id WHERE c.invalidated=0 AND s.item_id=?",
                        (item_id,),
                    ).fetchall()
                )
        for row in rows:
            conn.execute(
                "UPDATE checkpoints SET invalidated=1,summary='{}' WHERE id=?",
                (row["id"],),
            )
            conn.execute("DELETE FROM entries WHERE id=?", (row["id"],))

    def get_profile(self) -> dict:
        with self._lock:
            return self._profile(self._connect())

    def save_profile(
        self, patch: dict, *, expected_revision=None, source="settings"
    ) -> dict:
        if not isinstance(patch, dict) or set(patch) - {*PROFILE_FIELDS, "age"}:
            raise ValueError("profile patch contains unknown fields")
        if source not in {"settings", "voice", "explicit", "human"}:
            raise ValueError("profile changes require an explicit human source")
        _revision(expected_revision)
        cleaned = {
            key: _string(value, key, PROFILE_FIELDS[key], empty=True)
            for key, value in patch.items()
            if key != "age"
        }
        if "age" in patch:
            cleaned["age"] = (
                None if patch["age"] is None else _integer(patch["age"], "age", 0, 130)
            )
        with self._transaction() as conn:
            current = self._profile(conn)
            if (
                expected_revision is not None
                and current["revision"] != expected_revision
            ):
                raise MemoryConflict("Profile changed. Refresh before saving again.")
            if cleaned:
                current.update(cleaned)
                conn.execute(
                    "UPDATE profile SET name=?,age=?,occupation=?,preferences=?,revision=revision+1,updated_at=?,source=? WHERE id=1",
                    (
                        current["name"],
                        current["age"],
                        current["occupation"],
                        current["preferences"],
                        _now(),
                        source,
                    ),
                )
                self._advance(conn)
                self._invalidate_checkpoints(conn)
            return self._profile(conn)

    def get_settings(self) -> dict:
        with self._lock:
            return self._settings(self._connect())

    def save_settings(self, patch: dict, *, expected_revision=None) -> dict:
        if not isinstance(patch, dict) or set(patch) - (
            set(DEFAULT_SETTINGS) - {"revision"}
        ):
            raise ValueError("settings patch contains unknown fields")
        _revision(expected_revision)
        with self._transaction() as conn:
            current = self._settings(conn)
            if (
                expected_revision is not None
                and current["revision"] != expected_revision
            ):
                raise MemoryConflict(
                    "Memory settings changed. Refresh before saving again."
                )
            candidate = {**current, **patch}
            if type(candidate["enabled"]) is not bool:
                raise ValueError("enabled must be a boolean")
            budget = _integer(
                candidate["context_budget"], "context_budget", 4096, 128000
            )
            _integer(
                candidate["summary_trigger"], "summary_trigger", 1024, budget - 2048
            )
            _integer(candidate["recent_turns"], "recent_turns", 4, 40)
            model = _string(candidate["summarizer_model"], "summarizer_model", 120)
            if not all(
                character.isprintable() and not character.isspace()
                for character in model
            ):
                raise ValueError("summarizer_model must be a safe printable identifier")
            candidate["summarizer_model"] = model
            if patch:
                candidate["revision"] += 1
                conn.execute(
                    "UPDATE settings SET value=? WHERE id=1", (json.dumps(candidate),)
                )
            return candidate

    def memory_revision(self) -> int:
        with self._lock:
            return self._memory_revision(self._connect())

    def start_conversation(self, conversation_id=None) -> str:
        conversation_id = _string(
            conversation_id or str(uuid4()), "conversation ID", 300
        )
        with self._transaction() as conn:
            previous = conn.execute(
                "SELECT status FROM conversations WHERE id=?", (conversation_id,)
            ).fetchone()
            writer = json.loads(previous["status"]).get("_writer") if previous else None
            if self._writer_alive(writer) and writer.get("id") != self._status_owner:
                raise MemoryConflict(
                    "Another live session owns this conversation. Start a new conversation."
                )
            persisted = {"state": "collecting", "_writer": self._writer_metadata()}
            conn.execute(
                "INSERT OR IGNORE INTO conversations(id,started_at,status) VALUES(?,?,?)",
                (conversation_id, _now(), json.dumps(persisted)),
            )
            conn.execute(
                "UPDATE conversations SET status=?,ended_at=NULL WHERE id=?",
                (json.dumps(persisted), conversation_id),
            )
            self._owned_conversations.add(conversation_id)
            self._live_status[conversation_id] = {"state": "collecting"}
            _ACTIVE_STATUS_OWNERS[self._status_owner] = self
        return conversation_id

    def latest_conversation_id(self) -> str | None:
        """Recover journal continuity without trusting a previous live status."""
        with self._lock:
            row = (
                self._connect()
                .execute(
                    "SELECT c.id FROM conversations c WHERE EXISTS(SELECT 1 FROM journal j WHERE j.conversation_id=c.id AND j.excluded=0) ORDER BY c.started_at DESC LIMIT 1"
                )
                .fetchone()
            )
            return None if row is None else row["id"]

    def recoverable_conversation_id(self) -> str | None:
        """Find a usable journal whose session owner has closed or died.

        start_conversation claims it transactionally; another concurrent
        recovery may win between this read and that claim.
        """
        with self._lock:
            rows = self._connect().execute(
                "SELECT c.id,c.status FROM conversations c WHERE EXISTS(SELECT 1 FROM journal j WHERE j.conversation_id=c.id AND j.excluded=0) ORDER BY c.started_at DESC"
            )
            for row in rows:
                if not self._writer_alive(json.loads(row["status"]).get("_writer")):
                    return row["id"]
            return None

    def append_item(
        self,
        conversation_id,
        item_id,
        role,
        text,
        *,
        kind="message",
        trusted=False,
        tool_call_id=None,
        tool_name=None,
        status=None,
    ) -> dict:
        conversation_id = _string(conversation_id, "conversation ID", 300)
        item_id = _string(item_id, "item ID", 300)
        role = _string(role, "role", 40)
        # Event text is an archive, including spaces at boundaries when a long
        # input is split into bounded records. Validation must not normalize it.
        _string(text, "text", MAX_JOURNAL_CHARS, empty=True)
        kind = _string(kind, "kind", 80)
        if type(trusted) is not bool:
            raise ValueError("trusted must be a boolean")
        tool_call_id = (
            None if tool_call_id is None else _string(tool_call_id, "tool_call_id", 300)
        )
        tool_name = None if tool_name is None else _string(tool_name, "tool_name", 200)
        status = None if status is None else _string(status, "status", 80)
        with self._transaction() as conn:
            if (
                conn.execute(
                    "SELECT 1 FROM conversations WHERE id=?", (conversation_id,)
                ).fetchone()
                is None
            ):
                raise ValueError("Unknown conversation")
            previous = conn.execute(
                "SELECT * FROM journal WHERE conversation_id=? AND item_id=?",
                (conversation_id, item_id),
            ).fetchone()
            if previous is not None:
                return _journal(previous)
            excluded = (
                self._excluded(conn, text)
                or conn.execute(
                    "SELECT 1 FROM exclusions WHERE item_id=?", (item_id,)
                ).fetchone()
                is not None
            )
            cursor = conn.execute(
                "INSERT INTO journal(conversation_id,item_id,role,text,kind,trusted,tool_call_id,tool_name,status,created_at,excluded) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    conversation_id,
                    item_id,
                    role,
                    "" if excluded else text,
                    kind,
                    trusted,
                    tool_call_id,
                    tool_name,
                    status,
                    _now(),
                    excluded,
                ),
            )
            if not excluded and text:
                self._put_entry(
                    conn,
                    f"journal:{cursor.lastrowid}",
                    "journal",
                    text,
                    "trusted human"
                    if trusted and role == "user"
                    else f"journal:{role}",
                )
            return _journal(
                conn.execute(
                    "SELECT * FROM journal WHERE seq=?", (cursor.lastrowid,)
                ).fetchone()
            )

    def journal(
        self, conversation_id, *, after=0, through=None, limit=1000
    ) -> list[dict]:
        conversation_id = _string(conversation_id, "conversation ID", 300)
        _integer(after, "after", 0, 2**63 - 1)
        _integer(limit, "limit", 1, 1000)
        if through is not None:
            _integer(through, "through", after, 2**63 - 1)
        with self._lock:
            rows = (
                self._connect()
                .execute(
                    "SELECT * FROM journal WHERE conversation_id=? AND excluded=0 AND seq>? AND (? IS NULL OR seq<=?) ORDER BY seq LIMIT ?",
                    (conversation_id, after, through, through, limit),
                )
                .fetchall()
            )
            return [_journal(row) for row in rows]

    @staticmethod
    def _latest(conn, conversation_id=None):
        return conn.execute(
            "SELECT * FROM checkpoints WHERE invalidated=0 AND (? IS NULL OR conversation_id=?) ORDER BY created_at DESC,through_seq DESC LIMIT 1",
            (conversation_id, conversation_id),
        ).fetchone()

    def latest_checkpoint(self, conversation_id=None) -> dict | None:
        with self._lock:
            return _checkpoint(self._latest(self._connect(), conversation_id))

    def _validate_summary(
        self, conn, conversation_id, through_seq, summary, source_item_ids
    ):
        if not isinstance(summary, dict) or set(summary) != {
            "overview",
            *SUMMARY_SECTIONS,
        }:
            raise ValueError(
                "summary must contain overview and all structured sections"
            )
        cleaned = {
            "overview": _string(summary["overview"], "overview", 6000, empty=True)
        }
        supplied = set(_sources(source_item_ids))
        previous = _checkpoint(
            self._latest(conn, conversation_id) or self._latest(conn)
        )
        previous_ids = set()
        if previous:
            for section in SUMMARY_SECTIONS:
                for entry in previous["summary"][section]:
                    previous_ids.update(entry["source_item_ids"])
        available = {}
        for item_id in supplied:
            row = conn.execute(
                "SELECT * FROM journal WHERE conversation_id=? AND item_id=? AND seq<=? AND excluded=0",
                (conversation_id, item_id, through_seq),
            ).fetchone()
            if row is None and item_id in previous_ids:
                row = conn.execute(
                    "SELECT * FROM journal WHERE item_id=? AND excluded=0 ORDER BY seq DESC LIMIT 1",
                    (item_id,),
                ).fetchone()
            if (
                row is None
                or conn.execute(
                    "SELECT 1 FROM exclusions WHERE item_id=?", (item_id,)
                ).fetchone()
            ):
                raise ValueError("summary references an unknown or deleted source")
            available[item_id] = row
        for section in SUMMARY_SECTIONS:
            entries = summary[section]
            if not isinstance(entries, list) or len(entries) > 100:
                raise ValueError(f"{section} must be a bounded list")
            cleaned[section] = []
            for entry in entries:
                if not isinstance(entry, dict) or set(entry) != {
                    "content",
                    "source_item_ids",
                }:
                    raise ValueError(
                        "summary entries require content and source_item_ids"
                    )
                content = _string(entry["content"], "summary content", MAX_FACT_CHARS)
                ids = _sources(entry["source_item_ids"])
                if not ids or not set(ids) <= supplied:
                    raise ValueError("summary entry references an unknown source")
                if self._excluded(conn, content):
                    raise ValueError("summary contains deleted content")
                records = [available[item_id] for item_id in ids]
                if section == "facts" and any(
                    not row["trusted"]
                    or row["role"] != "user"
                    or row["kind"] not in {"message", "transcript"}
                    for row in records
                ):
                    raise ValueError("Fact claims require trusted human source items")
                if section == "outcomes":
                    if any(
                        not row["trusted"]
                        or (row["kind"] not in _TOOL_KINDS and row["role"] != "tool")
                        or row["status"] not in _TERMINAL
                        for row in records
                    ):
                        raise ValueError(
                            "outcome claims require terminal trusted tool evidence"
                        )
                    failures = any(row["status"] not in _SUCCESS for row in records)
                    describes_failure = re.search(
                        r"\b(fail\w*|error\w*|interrupt\w*|cancel\w*|uncertain|unverified|timeout|timed out|not completed|could not|did not)\b",
                        content,
                        re.IGNORECASE,
                    )
                    affirmative = re.sub(
                        r"\bnot completed\b", "", content, flags=re.IGNORECASE
                    )
                    claims_success = re.search(
                        r"\b(success\w*|succeeded|completed|done|verified successfully)\b",
                        affirmative,
                        re.IGNORECASE,
                    )
                    if failures and (not describes_failure or claims_success):
                        raise ValueError(
                            "successful outcome contradicts its tool evidence"
                        )
                cleaned[section].append({"content": content, "source_item_ids": ids})
        if self._excluded(conn, cleaned["overview"]):
            raise ValueError("summary overview contains deleted content")
        if len(json.dumps(cleaned, ensure_ascii=False)) > MAX_SUMMARY_CHARS:
            raise ValueError("summary exceeds its bounded storage budget")
        return cleaned, available

    def save_checkpoint(
        self,
        conversation_id,
        *,
        through_seq,
        summary,
        expected_memory_revision,
        source_item_ids,
    ) -> dict:
        conversation_id = _string(conversation_id, "conversation ID", 300)
        _integer(through_seq, "through_seq", 1, 2**63 - 1)
        _integer(expected_memory_revision, "expected_memory_revision", 0, 2**63 - 1)
        with self._transaction() as conn:
            revision = self._memory_revision(conn)
            if revision != expected_memory_revision:
                raise MemoryConflict(
                    "Memory changed while the summary was running. Retry from current sources."
                )
            last = conn.execute(
                "SELECT max(seq) FROM journal WHERE conversation_id=?",
                (conversation_id,),
            ).fetchone()[0]
            if last is None or through_seq > last:
                raise ValueError("checkpoint watermark is outside the journal")
            previous = self._latest(conn, conversation_id)
            if previous is not None and through_seq < previous["through_seq"]:
                raise MemoryConflict("A newer checkpoint already covers this prefix")
            cleaned, available = self._validate_summary(
                conn, conversation_id, through_seq, summary, source_item_ids
            )
            memory_id = "checkpoint:" + str(uuid4())
            conn.execute(
                "INSERT INTO checkpoints VALUES(?,?,?,?,?,?,0)",
                (
                    memory_id,
                    conversation_id,
                    through_seq,
                    json.dumps(cleaned, ensure_ascii=False),
                    revision,
                    _now(),
                ),
            )
            conn.executemany(
                "INSERT INTO checkpoint_sources VALUES(?,?)",
                ((memory_id, item_id) for item_id in available),
            )
            self._put_entry(
                conn, memory_id, "checkpoint", _summary_text(cleaned), "summary"
            )
            conn.execute(
                "UPDATE conversations SET checkpoint_revision=checkpoint_revision+1 WHERE id=?",
                (conversation_id,),
            )
            for fact in cleaned["facts"]:
                sources = [available[item_id] for item_id in fact["source_item_ids"]]
                if all(
                    row["trusted"]
                    and row["role"] == "user"
                    and row["kind"] in {"message", "transcript"}
                    for row in sources
                ):
                    self._add_fact(
                        conn,
                        fact["content"],
                        target="memory",
                        source="summary",
                        source_item_ids=fact["source_item_ids"],
                        revision=revision,
                    )
            return _checkpoint(
                conn.execute(
                    "SELECT * FROM checkpoints WHERE id=?", (memory_id,)
                ).fetchone()
            )

    def list_memories(self, query="", limit=100) -> list[dict]:
        query = _string(query, "query", 400, empty=True)
        _integer(limit, "limit", 1, 100)
        with self._lock:
            conn = self._connect()
            if not query:
                rows = conn.execute(
                    "SELECT * FROM entries ORDER BY created_at DESC,id LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                tokens = re.findall(r"\w+", query, re.UNICODE)[:20]
                if not tokens:
                    return []
                match = " AND ".join(
                    '"' + token.replace('"', '""') + '"' for token in tokens
                )
                rows = conn.execute(
                    "SELECT e.* FROM memory_search m JOIN entries e ON e.id=m.id WHERE memory_search MATCH ? ORDER BY rank,e.created_at DESC LIMIT ?",
                    (match, limit),
                ).fetchall()
            return [_entry(row) for row in rows]

    def _remove(self, conn, records):
        pending = list(records)
        removed = set()
        source_ids = set()
        excluded_sources = set()
        while pending:
            for record in pending:
                if record["id"] in removed:
                    continue
                removed.add(record["id"])
                if record["kind"] == "fact":
                    source_ids.update(
                        row[0]
                        for row in conn.execute(
                            "SELECT item_id FROM fact_sources WHERE fact_id=?",
                            (record["id"],),
                        )
                    )
                elif record["kind"] == "checkpoint":
                    source_ids.update(
                        row[0]
                        for row in conn.execute(
                            "SELECT item_id FROM checkpoint_sources WHERE checkpoint_id=?",
                            (record["id"],),
                        )
                    )
                elif record["kind"] == "journal":
                    row = conn.execute(
                        "SELECT item_id FROM journal WHERE seq=?",
                        (int(record["id"].split(":")[1]),),
                    ).fetchone()
                    if row:
                        source_ids.add(row["item_id"])
                normalized = _normalized(record["content"])
                if normalized:
                    conn.execute(
                        "DELETE FROM explicit_overrides WHERE normalized=?",
                        (normalized,),
                    )
                    conn.execute(
                        "INSERT OR IGNORE INTO tombstones VALUES(?,?)",
                        (normalized, _now()),
                    )
                conn.execute(
                    "UPDATE facts SET deleted=1,revision=? WHERE id=?",
                    (self._memory_revision(conn), record["id"]),
                )
                conn.execute("DELETE FROM entries WHERE id=?", (record["id"],))
                if record["kind"] == "checkpoint":
                    conn.execute(
                        "UPDATE checkpoints SET invalidated=1,summary='{}' WHERE id=?",
                        (record["id"],),
                    )
            pending = []
            for item_id in source_ids - excluded_sources:
                excluded_sources.add(item_id)
                conn.execute(
                    "INSERT OR IGNORE INTO exclusions VALUES(?,?)", (item_id, _now())
                )
                for row in conn.execute(
                    "SELECT seq,text FROM journal WHERE item_id=?", (item_id,)
                ).fetchall():
                    if row["text"]:
                        conn.execute(
                            "INSERT OR IGNORE INTO tombstones VALUES(?,?)",
                            (_normalized(row["text"]), _now()),
                        )
                    conn.execute(
                        "DELETE FROM entries WHERE id=?", (f"journal:{row['seq']}",)
                    )
                conn.execute(
                    "UPDATE journal SET excluded=1,text='' WHERE item_id=?", (item_id,)
                )
                pending.extend(
                    conn.execute(
                        "SELECT e.* FROM entries e JOIN fact_sources s ON s.fact_id=e.id WHERE s.item_id=?",
                        (item_id,),
                    ).fetchall()
                )
            self._invalidate_checkpoints(conn, excluded_sources)
            # Content exclusions also remove copies that never carried source links.
            for row in conn.execute(
                "SELECT DISTINCT e.* FROM entries e JOIN tombstones t ON instr(e.normalized,t.normalized)>0 OR instr(t.normalized,e.normalized)>0"
            ).fetchall():
                if not self._excluded(conn, row["content"]):
                    continue
                if row["kind"] == "checkpoint":
                    conn.execute(
                        "UPDATE checkpoints SET invalidated=1,summary='{}' WHERE id=?",
                        (row["id"],),
                    )
                    conn.execute("DELETE FROM entries WHERE id=?", (row["id"],))
                else:
                    pending.append(row)

    def delete_memory(self, memory_id: str) -> bool:
        memory_id = _string(memory_id, "memory ID", 300)
        with self._transaction() as conn:
            record = conn.execute(
                "SELECT * FROM entries WHERE id=?", (memory_id,)
            ).fetchone()
            if record is None:
                return False
            self._advance(conn)
            self._remove(conn, [record])
            return True

    def clear_conversation_memory(self) -> None:
        with self._transaction() as conn:
            self._advance(conn)
            self._remove(conn, conn.execute("SELECT * FROM entries").fetchall())
            self._invalidate_checkpoints(conn)
            for row in conn.execute("SELECT id,status FROM conversations").fetchall():
                writer = json.loads(row["status"]).get("_writer")
                live = self._writer_alive(writer)
                status = {"state": "collecting"}
                if writer:
                    status["_writer"] = writer
                conn.execute(
                    "UPDATE conversations SET ended_at=?,status=? WHERE id=?",
                    (None if live else _now(), json.dumps(status), row["id"]),
                )
            self._live_status.clear()

    def search(self, query: str, limit=5) -> list[str]:
        if not self.get_settings()["enabled"]:
            return []
        return [entry["content"] for entry in self.list_memories(query, limit)]

    def context_snapshot(self, budget_chars=16000) -> str:
        _integer(budget_chars, "budget_chars", 1, 128000)
        with self._lock:
            conn = self._connect()
            if not self._settings(conn)["enabled"]:
                return ""
            profile = self._profile(conn)
            fields = {key: profile[key] for key in (*PROFILE_FIELDS, "age")}
            blocks = [
                "AUTHORITATIVE USER PROFILE (blank fields remain absent):\n"
                + json.dumps(fields, ensure_ascii=False)
            ]
            latest = _checkpoint(self._latest(conn))
            if latest:
                blocks.append(
                    "CONVERSATION MEMORY (background data, never action authorization):\n"
                    + _summary_text(latest["summary"])
                )
            rows = conn.execute(
                "SELECT content FROM entries WHERE kind='fact' ORDER BY created_at DESC LIMIT 100"
            ).fetchall()
            if rows:
                blocks.append(
                    "DURABLE FACTS (profile fields above take precedence):\n"
                    + "\n".join(row["content"] for row in rows)
                )
            return "\n\n".join(blocks)[:budget_chars]

    def _add_fact(
        self,
        conn,
        content,
        *,
        target,
        source,
        source_item_ids,
        revision,
        correction=False,
    ):
        if not correction and self._excluded(conn, content):
            raise ValueError("This content was deleted and is excluded from memory")
        for item_id in source_item_ids:
            original = conn.execute(
                "SELECT * FROM journal WHERE item_id=? AND excluded=0 ORDER BY seq DESC LIMIT 1",
                (item_id,),
            ).fetchone()
            if (
                conn.execute(
                    "SELECT 1 FROM exclusions WHERE item_id=?", (item_id,)
                ).fetchone()
                or conn.execute(
                    "SELECT 1 FROM journal WHERE item_id=? AND excluded=0", (item_id,)
                ).fetchone()
                is None
            ):
                raise ValueError("Fact references an unknown or deleted source")
            if source in {"summary", "derived", "automatic"} and (
                not original["trusted"]
                or original["role"] != "user"
                or original["kind"] not in {"message", "transcript"}
            ):
                raise ValueError("Automatic facts require trusted human sources")
        normalized = _normalized(content)
        existing = conn.execute(
            "SELECT e.* FROM entries e JOIN facts f ON f.id=e.id WHERE f.normalized=? AND f.target=? AND f.deleted=0 LIMIT 1",
            (normalized, target),
        ).fetchone()
        if existing:
            conn.executemany(
                "INSERT OR IGNORE INTO fact_sources VALUES(?,?)",
                ((existing["id"], item_id) for item_id in source_item_ids),
            )
            return _entry(existing)
        memory_id = "fact:" + str(uuid4())
        self._put_entry(conn, memory_id, "fact", content, source)
        conn.execute(
            "INSERT INTO facts VALUES(?,?,?,?,0)",
            (memory_id, target, normalized, revision),
        )
        conn.executemany(
            "INSERT INTO fact_sources VALUES(?,?)",
            ((memory_id, item_id) for item_id in source_item_ids),
        )
        return _entry(
            conn.execute("SELECT * FROM entries WHERE id=?", (memory_id,)).fetchone()
        )

    def list_facts(self, target=None, *, limit=100) -> list[dict]:
        if target is not None and target not in {"memory", "user"}:
            raise ValueError("target must be memory or user")
        _integer(limit, "limit", 1, 100)
        with self._lock:
            rows = (
                self._connect()
                .execute(
                    "SELECT e.* FROM entries e JOIN facts f ON f.id=e.id WHERE f.deleted=0 AND (? IS NULL OR f.target=?) ORDER BY e.created_at DESC LIMIT ?",
                    (target, target, limit),
                )
                .fetchall()
            )
            return [_entry(row) for row in rows]

    def correct_fact(
        self,
        old_content,
        new_content,
        *,
        target="memory",
        source="explicit",
        expected_memory_revision=None,
    ) -> dict:
        """Commit an explicit correction and its old-content exclusion together."""
        old_content = _string(old_content, "old_content", MAX_FACT_CHARS)
        new_content = _string(new_content, "new_content", MAX_FACT_CHARS)
        if target not in {"memory", "user"}:
            raise ValueError("target must be memory or user")
        source = _string(source, "source", 120)
        if source in {"summary", "derived", "automatic"}:
            raise ValueError("Corrections require explicit human input")
        _revision(expected_memory_revision)
        with self._transaction() as conn:
            if (
                expected_memory_revision is not None
                and self._memory_revision(conn) != expected_memory_revision
            ):
                raise MemoryConflict("Memory changed. Refresh before correcting it.")
            if self._excluded(conn, new_content):
                raise ValueError("The replacement contains previously deleted content")
            records = conn.execute(
                "SELECT * FROM entries WHERE normalized=?", (_normalized(old_content),)
            ).fetchall()
            revision = self._advance(conn)
            self._remove(
                conn,
                records
                or [
                    {
                        "id": "legacy:" + str(uuid4()),
                        "kind": "legacy",
                        "content": old_content,
                    }
                ],
            )
            result = self._add_fact(
                conn,
                new_content,
                target=target,
                source=source,
                source_item_ids=[],
                revision=revision,
                correction=True,
            )
            # A human revision may contain unchanged parts of the old text.
            # This exact new content is allowed; old sources remain excluded.
            conn.execute(
                "INSERT OR REPLACE INTO explicit_overrides VALUES(?,?)",
                (_normalized(new_content), _now()),
            )
            return result

    def add_fact(
        self, content: str, *, target="memory", source="explicit", source_item_ids=None
    ) -> dict:
        content = _string(content, "content", MAX_FACT_CHARS)
        if target not in {"memory", "user"}:
            raise ValueError("target must be memory or user")
        source = _string(source, "source", 120)
        ids = _sources(source_item_ids)
        if source in {"summary", "derived", "automatic"} and not ids:
            raise ValueError("Automatic facts require original sources")
        with self._transaction() as conn:
            record = self._add_fact(
                conn,
                content,
                target=target,
                source=source,
                source_item_ids=ids,
                revision=self._memory_revision(conn) + 1,
            )
            self._advance(conn)
            return record

    def forget(self, query: str) -> bool:
        query = _string(query, "query", MAX_FACT_CHARS)
        normalized = _normalized(query)
        if not any(character.isalnum() for character in normalized):
            raise ValueError("query must contain letters or numbers")
        with self._transaction() as conn:
            records = conn.execute(
                "SELECT * FROM entries WHERE instr(normalized,?)>0", (normalized,)
            ).fetchall()
            inserted = conn.execute(
                "INSERT OR IGNORE INTO tombstones VALUES(?,?)", (normalized, _now())
            ).rowcount
            if records or inserted:
                self._advance(conn)
                self._remove(conn, records)
                # An unmatched legacy deletion still makes the old summary stale.
                self._invalidate_checkpoints(conn)
            return bool(records)

    def excluded(self, content: str) -> bool:
        content = _string(content, "content", 128000, empty=True)
        with self._lock:
            return self._excluded(self._connect(), content)

    def set_context_status(self, conversation_id, status: dict):
        conversation_id = _string(conversation_id, "conversation ID", 300)
        if not isinstance(status, dict):
            raise ValueError("status must be a JSON object")
        try:
            encoded = json.dumps(status, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("status must contain JSON primitives") from exc
        if len(encoded) > 12000:
            raise ValueError("status is too large")
        persisted = json.loads(encoded)
        persisted["_writer"] = self._writer_metadata()
        with self._transaction() as conn:
            previous = conn.execute(
                "SELECT status FROM conversations WHERE id=?", (conversation_id,)
            ).fetchone()
            if not previous:
                raise ValueError("Unknown conversation")
            writer = json.loads(previous["status"]).get("_writer")
            if self._writer_alive(writer) and writer.get("id") != self._status_owner:
                raise MemoryConflict("Another live session owns this conversation")
            conn.execute(
                "UPDATE conversations SET status=? WHERE id=?",
                (json.dumps(persisted), conversation_id),
            )
            self._live_status[conversation_id] = json.loads(encoded)
            self._owned_conversations.add(conversation_id)
            _ACTIVE_STATUS_OWNERS[self._status_owner] = self

    def _writer_metadata(self):
        return {
            "pid": os.getpid(),
            "started_at": _PROCESS_STARTED_AT,
            "id": self._status_owner,
        }

    @staticmethod
    def _writer_alive(writer):
        if not isinstance(writer, dict) or writer.get("closed") is True:
            return False
        if writer.get("pid") == os.getpid():
            return writer.get("id") in _ACTIVE_STATUS_OWNERS
        try:
            return psutil.Process(writer["pid"]).create_time() == writer["started_at"]
        except (psutil.Error, KeyError, TypeError, ValueError):
            return False

    def get_status(self) -> dict:
        with self._lock:
            conn = self._connect()
            settings = self._settings(conn)
            row = conn.execute(
                "SELECT id,status FROM conversations ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            status = (
                {"state": "collecting"} if row is None else json.loads(row["status"])
            )
            writer = status.pop("_writer", None)
            if (
                row
                and not self._writer_alive(writer)
                and status.get("state")
                in {
                    "summarizing",
                    "waiting",
                    "waiting_to_compact",
                    "compacting",
                    "ready",
                    "restored",
                }
            ):
                status = {"state": "collecting", "recovered": True}
            if not settings["enabled"]:
                status = {"state": "disabled"}
            return {
                "settings": settings,
                "memory_revision": self._memory_revision(conn),
                "checkpoint": _checkpoint(self._latest(conn)),
                "status": status,
            }

    def close(self) -> None:
        with self._lock:
            if self._conn is not None and self._owned_conversations:
                try:
                    with self._transaction() as conn:
                        for conversation_id in self._owned_conversations:
                            row = conn.execute(
                                "SELECT status FROM conversations WHERE id=?",
                                (conversation_id,),
                            ).fetchone()
                            if not row:
                                continue
                            status = json.loads(row["status"])
                            writer = status.get("_writer")
                            if (
                                isinstance(writer, dict)
                                and writer.get("id") == self._status_owner
                            ):
                                writer["closed"] = True
                                conn.execute(
                                    "UPDATE conversations SET ended_at=?,status=? WHERE id=?",
                                    (
                                        _now(),
                                        json.dumps(
                                            {"state": "collecting", "_writer": writer}
                                        ),
                                        conversation_id,
                                    ),
                                )
                except sqlite3.Error:
                    # Failed ownership release stays conservatively live until
                    # process exit, while closing the connection still succeeds.
                    pass
            self._owned_conversations.clear()
            self._live_status.clear()
            _ACTIVE_STATUS_OWNERS.pop(self._status_owner, None)
            if self._conn is not None:
                self._conn.close()
                self._conn = None
