"""Durable expanded profile behavior across storage, memory and bridge consumers."""

import json
import sqlite3

import pytest

from personal_memory import MemoryConflict, PersonalMemoryStore
from personal_memory_agent import PersistentMemoryFacade

EXTRA_PROFILE = {
    "location": "Hyderabad, India",
    "languages": "English, Telugu",
    "interests": "Robotics and astronomy",
    "goals": "Build a helpful voice assistant",
    "about_me": "I enjoy learning by building small projects.",
}


def test_v1_profile_migration_preserves_existing_fields_and_revisions(tmp_path):
    path = tmp_path / "v1.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE profile (
                id INTEGER PRIMARY KEY CHECK(id=1), name TEXT NOT NULL DEFAULT '',
                age INTEGER, occupation TEXT NOT NULL DEFAULT '',
                preferences TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT, source TEXT NOT NULL DEFAULT 'settings'
            );
            INSERT INTO profile VALUES(
                1,'Avery',31,'Engineer','Concise replies',7,'2026-10-01T00:00:00Z','voice'
            );
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
            INSERT INTO metadata VALUES('memory_revision',11);
            PRAGMA user_version=1;
            """
        )
    store = PersonalMemoryStore(path)
    try:
        profile = store.get_profile()
        assert profile == {
            "name": "Avery",
            "age": 31,
            "occupation": "Engineer",
            "preferences": "Concise replies",
            "location": "",
            "languages": "",
            "interests": "",
            "goals": "",
            "about_me": "",
            "revision": 7,
            "updated_at": "2026-10-01T00:00:00Z",
        }
        assert store.memory_revision() == 11
        store.save_profile(EXTRA_PROFILE, expected_revision=7)
    finally:
        store.close()
    reopened = PersonalMemoryStore(path)
    try:
        assert reopened.get_profile()["about_me"] == EXTRA_PROFILE["about_me"]
        assert reopened.get_profile()["name"] == "Avery"
        assert reopened.get_profile()["revision"] == 8
        with sqlite3.connect(path) as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
            assert (
                conn.execute("SELECT source FROM profile").fetchone()[0] == "settings"
            )
    finally:
        reopened.close()


@pytest.mark.parametrize("customized", [False, True])
def test_v1_default_context_settings_upgrade_preserves_customized_settings(
    tmp_path, customized
):
    path = tmp_path / "v1.sqlite3"
    previous = {
        "enabled": True,
        "context_budget": 16000,
        "summary_trigger": 12000,
        "recent_turns": 12,
        "summarizer_model": "gemini-3.8-flash",
        "revision": 0,
    }
    if customized:
        previous.update(recent_turns=8, revision=3)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE settings(id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO settings VALUES(1,?)", (json.dumps(previous),))
        conn.execute("PRAGMA user_version=1")
    store = PersonalMemoryStore(path)
    try:
        settings = store.get_settings()
        if customized:
            assert settings == previous
        else:
            assert settings == {
                **previous,
                "context_budget": 20000,
                "summary_trigger": 16000,
                "revision": 1,
            }
    finally:
        store.close()


def test_expanded_profile_partial_save_clear_and_conflict_are_durable(tmp_path):
    path = tmp_path / "personal.sqlite3"
    store = PersonalMemoryStore(path)
    try:
        saved = store.save_profile({"name": "Avery", **EXTRA_PROFILE})
        assert all(saved[field] == value for field, value in EXTRA_PROFILE.items())
        token = store.memory_revision()
        cleared = store.save_profile({"goals": "  "}, expected_revision=1)
        assert cleared["goals"] == ""
        assert cleared["location"] == "Hyderabad, India"
        assert store.memory_revision() == token + 1
        with pytest.raises(MemoryConflict):
            store.save_profile({"about_me": "Stale biography"}, expected_revision=1)
    finally:
        store.close()
    reopened = PersonalMemoryStore(path)
    try:
        assert reopened.get_profile() == cleared
        snapshot = reopened.context_snapshot()
        assert '"languages": "English, Telugu"' in snapshot
        assert '"goals": ""' in snapshot
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "field,limit",
    [
        ("location", 200),
        ("languages", 200),
        ("interests", 1500),
        ("goals", 1500),
        ("about_me", 4000),
    ],
)
def test_expanded_profile_limits_are_atomic_and_allow_exact_boundary(
    tmp_path, field, limit
):
    store = PersonalMemoryStore(tmp_path / "personal.sqlite3")
    try:
        assert store.save_profile({field: "x" * limit})[field] == "x" * limit
        for invalid in ["x" * (limit + 1), None, True, 42, "bad\x00text"]:
            with pytest.raises(ValueError, match=field):
                store.save_profile({field: invalid, "name": "Should not write"})
        assert store.get_profile()["revision"] == 1
        assert store.get_profile()["name"] == ""
        assert store.memory_revision() == 1
    finally:
        store.close()


def test_expanded_profile_facade_supports_search_patch_replace_and_remove(tmp_path):
    store = PersonalMemoryStore(tmp_path / "personal.sqlite3")
    facade = PersistentMemoryFacade(store, None)
    try:
        store.save_profile(EXTRA_PROFILE)
        assert facade.search("Telugu", target="user") == ["Languages: English, Telugu"]
        assert facade.patch("user", "astronomy", "photography")["success"]
        assert store.get_profile()["interests"] == "Robotics and photography"
        assert facade.replace("user", "helpful voice", "Goals: Learn Telugu")["success"]
        assert store.get_profile()["goals"] == "Learn Telugu"
        assert facade.remove("user", "Hyderabad")["success"]
        assert store.get_profile()["location"] == ""
        profile_json = facade.snapshot(include_summary=False).split("\n", 1)[1]
        assert json.loads(profile_json)["about_me"] == EXTRA_PROFILE["about_me"]
    finally:
        store.close()


def test_legacy_expanded_profile_claims_cannot_refill_cleared_fields(tmp_path):
    from dual_memory import DualMemoryStore

    store = PersonalMemoryStore(tmp_path / "personal.sqlite3")
    legacy = DualMemoryStore(tmp_path / "legacy")
    facade = PersistentMemoryFacade(store, legacy)
    try:
        legacy.add("user", "Location: Oslo")
        legacy.add("user", "My languages are Norwegian")
        legacy.add("memory", "The user's goals are to move abroad")
        legacy.add("memory", "Interests: Sailing")
        legacy.add("user", "About me: I live in Oslo")
        legacy.add("user", "My bio is I learn by building projects")
        snapshot = facade.snapshot()
        assert "Oslo" not in snapshot
        assert "Norwegian" not in snapshot
        assert "move abroad" not in snapshot
        assert "Sailing" not in snapshot
        assert "I learn by building projects" not in snapshot
    finally:
        store.close()


@pytest.mark.parametrize(
    "text,field,want",
    [
        ("My location is Hyderabad", "location", "Hyderabad"),
        ("I live in Hyderabad", "location", "Hyderabad"),
        ("My languages are English and Telugu", "languages", "English and Telugu"),
        (
            "My interests are robotics and astronomy",
            "interests",
            "robotics and astronomy",
        ),
        ("My goals are to build IRA", "goals", "to build IRA"),
        (
            "About me: I learn by building projects",
            "about_me",
            "I learn by building projects",
        ),
    ],
)
def test_explicit_voice_profile_details_use_canonical_record(
    tmp_path, text, field, want
):
    store = PersonalMemoryStore(tmp_path / "personal.sqlite3")
    facade = PersistentMemoryFacade(store, None)
    try:
        assert facade.add("user", text)["success"]
        assert store.get_profile()[field] == want
        assert store.list_facts("user") == []
    finally:
        store.close()


def test_escaped_profile_snapshot_keeps_valid_json_and_full_recall(tmp_path):
    store = PersonalMemoryStore(tmp_path / "personal.sqlite3")
    facade = PersistentMemoryFacade(store, None)
    biography = '\\"\n' * 1320 + " BIOGRAPHY END"
    try:
        store.save_profile(
            {
                "name": "Avery",
                "preferences": '\\"\n' * 1300,
                "location": "",
                "languages": "English",
                "interests": '\\"\n' * 500,
                "goals": '\\"\n' * 500,
                "about_me": biography,
            }
        )
        snapshot = facade.snapshot(budget_chars=16000)
        fields = json.loads(snapshot.split("\n", 1)[1])
        assert len(snapshot) <= 16000
        assert fields["name"] == "Avery"
        assert fields["location"] == ""
        assert fields["languages"] == "English"
        assert "about_me" in fields["_truncated_fields"]
        assert facade.search("About me", target="user")[0] == "About me: " + biography
        assert store.get_profile()["about_me"] == biography
        smaller = json.loads(facade.snapshot(budget_chars=1200).split("\n", 1)[1])
        assert smaller["location"] == ""
        assert set(smaller).issuperset(
            {"name", "age", "occupation", "preferences", *EXTRA_PROFILE}
        )
    finally:
        store.close()


def test_multilingual_max_profile_stays_within_snapshot_byte_allowance(tmp_path):
    store = PersonalMemoryStore(tmp_path / "personal.sqlite3")
    facade = PersistentMemoryFacade(store, None)
    try:
        store.save_profile(
            {
                "name": "ఆ" * 120,
                "occupation": "ఆ" * 200,
                "preferences": "ఆ" * 4000,
                "location": "ఆ" * 200,
                "languages": "ఆ" * 200,
                "interests": "ఆ" * 1500,
                "goals": "ఆ" * 1500,
                "about_me": "ఆ" * 4000,
            }
        )
        store.add_fact("Workspace reference " + "x" * 3980, target="memory")
        snapshot = facade.snapshot(include_summary=False, budget_chars=16000)
        fields, _ = json.JSONDecoder().raw_decode(snapshot.split("\n", 1)[1])
        assert len(snapshot.encode("utf-8")) <= 16000
        assert "about_me" in fields["_truncated_fields"]
        assert store.get_profile()["about_me"] == "ఆ" * 4000
        assert len(store.context_snapshot(budget_chars=16000).encode("utf-8")) <= 16000
        assert len(facade.get_system_prompt_snapshot("user").encode("utf-8")) <= 16000
    finally:
        store.close()


@pytest.mark.parametrize("control", ["\x01", "\x1b", "\x7f"])
def test_profile_rejects_unsafe_controls_without_blocking_multiline_text(
    tmp_path, control
):
    store = PersonalMemoryStore(tmp_path / "personal.sqlite3")
    try:
        with pytest.raises(ValueError, match="control"):
            store.save_profile({"about_me": "unsafe" + control})
        assert store.get_profile()["revision"] == 0
        assert (
            store.save_profile({"about_me": "Line one\nLine two\tIndented"})["about_me"]
            == "Line one\nLine two\tIndented"
        )
    finally:
        store.close()
