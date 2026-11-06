"""Task persistence uses temporary SQLite files, including concurrent callers."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from tasks import TaskStore


@pytest.fixture
def store(tmp_path):
    instance = TaskStore(tmp_path / "tasks.db")
    yield instance
    instance.close()


def test_creates_database_lazily_and_respects_memory_path(tmp_path, monkeypatch):
    path = tmp_path / "profile" / "ira.db"
    monkeypatch.setenv("IRA_MEMORY_DB", str(path))
    instance = TaskStore()
    try:
        assert not path.exists()
        task = instance.add("Buy milk")
        assert path.exists()
        assert instance.upcoming() == [task]
    finally:
        instance.close()


def test_file_connection_uses_wal_and_waits_for_other_writers(store):
    conn = store.connect()
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_add_normalizes_title_and_returns_utc_creation_time(store):
    before = datetime.now(timezone.utc)
    task = store.add("  Buy\n milk\t and  bread  ")
    after = datetime.now(timezone.utc)
    assert task.id > 0
    assert task.title == "Buy milk and bread"
    assert task.done is False
    assert task.due_at is None
    assert task.created_at.endswith("Z")
    assert (
        before
        <= datetime.fromisoformat(task.created_at.replace("Z", "+00:00"))
        <= after
    )


@pytest.mark.parametrize("title", ["", " \n\t ", "x" * 501, None, 17, False])
def test_rejects_invalid_titles_without_writing(store, title):
    with pytest.raises(ValueError):
        store.add(title)
    assert store.upcoming() == []


def test_accepts_title_at_length_limit(store):
    assert store.add("x" * 500).title == "x" * 500


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-10-16T09:00:00+05:30", "2026-10-16T03:30:00Z"),
        ("2026-10-16T23:00:00-04:00", "2026-10-17T03:00:00Z"),
        ("2026-10-16T03:30:00Z", "2026-10-16T03:30:00Z"),
        ("2026-10-16T03:30:00.123456+00:00", "2026-10-16T03:30:00.123456Z"),
    ],
)
def test_due_dates_are_stored_as_utc(store, value, expected):
    task = store.add("Call the office", value)
    assert task.due_at == expected
    row = (
        store.connect()
        .execute("SELECT due_at FROM tasks WHERE id = ?", (task.id,))
        .fetchone()
    )
    assert row["due_at"] == expected


@pytest.mark.parametrize(
    "value",
    [
        "tomorrow",
        "2026-10-16",
        "2026-10-16T09:00:00",
        "2026-02-30T09:00:00Z",
        "2026-10-16T09:00:00+00:99",
        "2026-10-16T09:00:00+24:00",
        "0001-01-01T00:00:00+01:00",
        "",
        123,
        False,
        [],
    ],
)
def test_rejects_invalid_or_timezone_naive_due_dates(store, value):
    with pytest.raises(ValueError):
        store.add("Do the thing", value)
    assert store.upcoming() == []


def test_upcoming_orders_unfinished_dates_then_nulls_and_ties_by_id(store):
    undated_first = store.add("Undated one")
    later = store.add("Later", "2026-10-18T09:00:00Z")
    first = store.add("First", "2026-10-16T09:00:00+05:30")
    tied = store.add("Same time", "2026-10-16T03:30:00Z")
    finished = store.add("Already done", "2026-10-15T09:00:00Z")
    undated_second = store.add("Undated two")
    store.set_done(finished.id, True)
    assert store.upcoming() == [first, tied, later, undated_first, undated_second]
    assert store.upcoming(limit=2) == [first, tied]


def test_upcoming_sorts_fractional_seconds_chronologically(store):
    fraction = store.add("A fraction later", "2026-10-16T03:30:00.000001Z")
    whole_second = store.add("On the second", "2026-10-16T03:30:00Z")
    assert store.upcoming() == [whole_second, fraction]


def test_default_limit_is_eight(store):
    created = [store.add(f"Task {number}") for number in range(10)]
    assert store.upcoming() == created[:8]


@pytest.mark.parametrize("limit", [0, -1, 101, True, 1.5, "8"])
def test_upcoming_rejects_invalid_limits(store, limit):
    with pytest.raises(ValueError):
        store.upcoming(limit=limit)


def test_toggle_and_delete_persist_after_reopen(store):
    original = store.add("Check the report")
    done = store.set_done(original.id, True)
    assert done is not None and done.done is True
    assert done.title == original.title
    assert done.created_at == original.created_at
    store.close()
    assert store.upcoming() == []
    restored = store.set_done(original.id, False)
    assert restored == original
    assert store.delete(original.id) is True
    store.close()
    assert store.upcoming() == []
    assert store.delete(original.id) is False
    assert store.set_done(original.id, True) is None


@pytest.mark.parametrize("task_id", [0, -1, 2**63, True, 1.5, "1"])
def test_rejects_invalid_ids(store, task_id):
    with pytest.raises(ValueError):
        store.set_done(task_id, True)
    with pytest.raises(ValueError):
        store.delete(task_id)


@pytest.mark.parametrize("done", [None, 0, 1, "false", []])
def test_completion_requires_boolean(store, done):
    task = store.add("Still unfinished")
    with pytest.raises(ValueError):
        store.set_done(task.id, done)
    assert store.upcoming() == [task]


def test_closed_connection_releases_resources_and_can_reopen(store):
    conn = store.connect()
    store.close()
    store.close()
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")
    assert store.add("After reopening").title == "After reopening"


def test_same_store_can_create_and_update_from_concurrent_threads(store):
    def create_and_update(number):
        task = store.add(f"Task {number}")
        updated = store.set_done(task.id, number % 2 == 0)
        assert updated is not None
        return updated

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(create_and_update, range(40)))

    assert len({task.id for task in results}) == 40
    assert {task.title for task in store.upcoming(limit=100)} == {
        f"Task {number}" for number in range(1, 40, 2)
    }


def test_two_stores_share_tasks_without_overwriting_memory_tables(tmp_path):
    path = tmp_path / "shared.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE existing_memory (content TEXT)")
        conn.execute("INSERT INTO existing_memory VALUES ('keep this')")
    first, second = TaskStore(path), TaskStore(path)
    try:
        task = first.add("Written by voice")
        assert second.upcoming() == [task]
        second.set_done(task.id, True)
        assert first.upcoming() == []
        assert (
            first.connect().execute("SELECT content FROM existing_memory").fetchone()[0]
            == "keep this"
        )
    finally:
        first.close()
        second.close()
