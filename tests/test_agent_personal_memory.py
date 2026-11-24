"""Canonical profile startup and existing voice tool integration, offline only."""

import json
from types import SimpleNamespace

from agent import Assistant, _scoped_text_input
from context_summary import estimate_tokens
from dual_memory import DualMemoryStore
from personal_memory import PersonalMemoryStore
from session_memory import SessionMemoryIntegration
from skills import SkillCatalog
from tasks import TaskStore


def build_assistant(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "synthetic-test-key")
    store = PersonalMemoryStore(tmp_path / "personal.sqlite3")
    store.save_profile(
        {"name": "Alex", "occupation": "Designer", "preferences": "Short replies"}
    )
    assistant = Assistant(
        personal_memory=store,
        dual_memory=DualMemoryStore(tmp_path / "legacy"),
        skills_catalog=SkillCatalog(tmp_path / "skills"),
        tasks=TaskStore(":memory:"),
    )
    return assistant, store


def test_canonical_profile_is_available_before_the_first_greeting(
    tmp_path, monkeypatch
):
    assistant, store = build_assistant(tmp_path, monkeypatch)
    try:
        assert "Alex" in assistant.instructions
        assert "Designer" in assistant.instructions
        assert "Short replies" in assistant.instructions
        assert "AUTHORITATIVE" in assistant.instructions
        assert "Alex" not in assistant._memory_base_instructions
    finally:
        store.close()


async def test_explicit_voice_correction_updates_the_settings_profile(
    tmp_path, monkeypatch
):
    assistant, store = build_assistant(tmp_path, monkeypatch)
    try:
        result = await assistant.remember(None, "My name is Morgan")
        assert "Remembered" in result
        assert store.get_profile()["name"] == "Morgan"
        await assistant._refresh_memory_instructions()
        assert "Morgan" in assistant.instructions
        assert '"name": "Alex"' not in assistant.instructions
    finally:
        store.close()


async def test_typed_command_is_durable_before_request_scope_admission():
    calls = []

    async def record(text):
        calls.append(("journal", text))

    async def scoped(session, event):
        calls.append(("scope", event.text))

    assistant = SimpleNamespace(
        _context_memory=SimpleNamespace(record_human=record),
        _scoped_tools=SimpleNamespace(text_input_cb=scoped),
    )
    event = SimpleNamespace(text="Please remember my plans")
    await _scoped_text_input(SimpleNamespace(current_agent=assistant), event)
    assert calls == [("journal", event.text), ("scope", event.text)]


async def test_actual_assistant_default_budget_leaves_room_for_full_profile(
    tmp_path, monkeypatch
):
    assistant, store = build_assistant(tmp_path, monkeypatch)
    try:
        store.save_profile({"preferences": "Clear concise replies. " * 170})
        runtime = SessionMemoryIntegration(assistant, store, assistant._dual_memory)
        await runtime.prepare()
        assert "Clear concise replies" in assistant.instructions
        assert "clarify_request" in assistant.instructions
        assert "observation_id" in assistant.instructions
        assert runtime._base_tokens < 12000
        await runtime.aclose()
    finally:
        store.close()


async def test_actual_assistant_bounds_escaped_profile_and_retrieves_full_details(
    tmp_path, monkeypatch
):
    assistant, store = build_assistant(tmp_path, monkeypatch)
    biography = '\\"\n' * 1320 + " BIOGRAPHY END"
    runtime = SessionMemoryIntegration(assistant, store, assistant._dual_memory)
    try:
        store.save_profile(
            {
                "occupation": "\\" * 200,
                "location": "\\" * 200,
                "languages": "\\" * 200,
                "preferences": "\\" * 4000,
                "interests": "\\" * 1500,
                "goals": "\\" * 1500,
                "about_me": biography,
            }
        )
        await runtime.prepare()
        rendered = assistant.instructions.split("AUTHORITATIVE USER PROFILE DATA", 1)[
            1
        ].split("\n", 1)[1]
        fields, _ = json.JSONDecoder().raw_decode(rendered)
        assert fields["name"] == "Alex"
        assert "about_me" in fields["_truncated_fields"]
        assert (
            estimate_tokens(assistant.instructions) + runtime._tool_tokens()
            <= 20000 - 2048
        )
        recalled = await assistant.recall(None, "About me")
        assert biography in recalled
        assert store.get_profile()["about_me"] == biography
    finally:
        await runtime.summarizer.aclose()
        store.close()


async def test_custom_small_budget_refreshes_escaped_profile_correction_without_losing_journal(
    tmp_path, monkeypatch
):
    assistant, store = build_assistant(tmp_path, monkeypatch)
    store.save_settings({"context_budget": 16000, "summary_trigger": 12000})
    store.save_profile(
        {
            "name": "\\" * 120,
            "occupation": "\\" * 200,
            "preferences": "\\" * 4000,
            "location": "\\" * 200,
            "languages": "\\" * 200,
            "interests": "\\" * 1500,
            "goals": "\\" * 1500,
            "about_me": "\\" * 4000,
        }
    )
    runtime = SessionMemoryIntegration(assistant, store, assistant._dual_memory)
    try:
        await runtime.prepare()
        cid = runtime.controller.conversation_id
        store.append_item(
            cid, "pending", "user", "Discuss the next project", trusted=True
        )
        store.save_profile({"name": "Avery"})
        await runtime.tick()
        assert '"name": "Avery"' in assistant.instructions
        assert runtime.controller.status()["state"] != "error"
        assert store.journal(cid)[0]["text"] == "Discuss the next project"
        rendered = assistant.instructions.split("AUTHORITATIVE USER PROFILE DATA", 1)[
            1
        ].split("\n", 1)[1]
        fields, _ = json.JSONDecoder().raw_decode(rendered)
        assert "about_me" in fields["_truncated_fields"]
        assert (
            estimate_tokens(assistant.instructions) + runtime._tool_tokens()
            <= 16000 - 2048
        )
    finally:
        await runtime.summarizer.aclose()
        store.close()
