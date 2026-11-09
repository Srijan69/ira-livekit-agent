"""Regressions from the frozen evaluation; no live provider or desktop input."""

import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from request_scope import (
    ActionProposal,
    AdmissionDecision,
    RequestScope,
    ScopeViolation,
)
from request_scope_livekit import RequestScopeTools, _Binding


@pytest.fixture
def no_policy_key(monkeypatch):
    for key in ("GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI"):
        monkeypatch.delenv(key, raising=False)


async def test_default_scope_rejects_playlist_during_file_request(no_policy_key):
    scope = RequestScope()
    request = scope.begin_request(
        "Create eval_test/hello.txt containing hello from IRA", source_id="typed:1"
    )
    with pytest.raises(ScopeViolation):
        await scope.admit(
            request.id,
            ActionProposal(
                "spotify_play_playlist", {"playlist_name": "My Phonk Songs"}
            ),
        )
    assert scope.history == ()


async def test_default_scope_keeps_exact_launch_fallback(no_policy_key):
    scope = RequestScope()
    request = scope.begin_request("Open Notepad", source_id="typed:1")
    permit = await scope.admit(
        request.id, ActionProposal("open_app", {"app_name": "Notepad"})
    )
    scope.consume(permit)
    scope.check(permit)
    with pytest.raises(ScopeViolation):
        await scope.admit(
            request.id, ActionProposal("open_app", {"app_name": "Spotify"})
        )


async def test_default_scope_never_approves_unsupported_completion(no_policy_key):
    scope = RequestScope()
    request = scope.begin_request("Open Notepad", source_id="typed:1")
    with pytest.raises(ScopeViolation):
        await scope.assess_completion(request.id, "Notepad is open", {})
    scope.require(request.id)


async def test_default_scope_uses_configured_provider_to_reject_unrelated_effect(
    monkeypatch,
):
    from google import genai

    monkeypatch.setenv("GOOGLE_API_KEY", "offline-policy-fixture")
    seen = []

    async def generate_content(*, contents, **kwargs):
        payload = json.loads(contents[0].parts[0].text)
        seen.append(payload)
        return SimpleNamespace(
            text='{"decision":"deny","reason":"Playlist is unrelated to the file"}'
        )

    client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    )
    monkeypatch.setattr(genai, "Client", lambda **kwargs: client)
    scope = RequestScope()
    request = scope.begin_request("Create hello.txt", source_id="typed:1")
    with pytest.raises(ScopeViolation):
        await scope.admit(
            request.id,
            ActionProposal(
                "spotify_play_playlist", {"playlist_name": "My Phonk Songs"}
            ),
        )
    assert seen[0]["original_request"] == "Create hello.txt"
    assert seen[0]["proposed_action"]["name"] == "spotify_play_playlist"


async def test_structured_uncertain_launch_is_not_recorded_as_success():
    async def allow(*args):
        return AdmissionDecision("allow", "Fixture launch request")

    scope = RequestScope(allow)
    request = scope.begin_request("Open Notepad", source_id="typed:1")
    facade = RequestScopeTools(scope)

    async def launch_result():
        return json.dumps({"status": "uncertain", "verified": False})

    await facade._execute(
        _Binding(scope, request),
        "open_app",
        {"app_name": "Notepad"},
        launch_result,
    )
    assert scope.history[-1]["outcome"] == "uncertain"
    with pytest.raises(ScopeViolation, match="uncertain"):
        await scope.admit(
            request.id, ActionProposal("open_app", {"app_name": "Notepad"})
        )


@pytest.mark.parametrize("status", [[], {}])
async def test_malformed_tool_status_does_not_break_outcome_recording(status):
    async def allow(*args):
        return AdmissionDecision("allow", "Fixture operation")

    scope = RequestScope(allow)
    request = scope.begin_request("Read page", source_id="typed:1")

    async def result():
        return json.dumps({"status": status})

    await RequestScopeTools(scope)._execute(
        _Binding(scope, request), "read_page", {}, result, kind="observe"
    )
    assert scope.history[-1]["outcome"] == "returned"


async def test_open_app_returns_pending_verification_instead_of_success(monkeypatch):
    import agent

    target = SimpleNamespace(name="Notepad", app_id="Microsoft.WindowsNotepad_test!App")
    monkeypatch.setattr(agent, "_launch_via_start_menu", lambda name: target)
    result = json.loads(await agent.Assistant().open_app(None, "Notepad"))
    assert result["status"] == "uncertain"
    assert result["verified"] is False
    assert result["app_name"] == "Notepad"
    assert "not claim" in result["instruction"].lower()


async def test_bound_relative_task_persists_requested_local_time(tmp_path):
    from agent import Assistant
    from tasks import TaskStore

    async def allow(*args):
        return AdmissionDecision("allow", "Explicit task fixture")

    store = TaskStore(tmp_path / "tasks.db")
    events = []
    scope = RequestScope(allow)
    request = scope.begin_request(
        "Add Review the evaluation report tomorrow at 4:30 PM", source_id="typed:1"
    )
    assistant = Assistant(
        tasks=store,
        request_scope=scope,
        clock=lambda: datetime.fromisoformat("2026-11-09T18:00:00+05:30"),
        publish_event=events.append,
    )
    facade = RequestScopeTools(scope)

    async def add():
        return await assistant.add_task(
            None, "Review the evaluation report", "2026-11-10T16:30:00Z"
        )

    try:
        result = await facade._execute(
            _Binding(scope, request),
            "add_task",
            {"title": "Review the evaluation report", "due_at": "2026-11-10T16:30:00Z"},
            add,
        )
        assert store.upcoming()[0].due_at == "2026-11-10T11:00:00Z"
        data = json.loads(result)
        assert data["task"]["due_at"] == "2026-11-10T11:00:00Z"
        assert data["due_at_local"] == "2026-11-10T16:30:00+05:30"
        assert events == [{"type": "tasks.changed"}]
    finally:
        store.close()


async def test_ambiguous_bound_deadline_does_not_create_a_task(tmp_path):
    from agent import Assistant
    from tasks import TaskStore

    async def allow(*args):
        return AdmissionDecision("allow", "Explicit task fixture")

    store = TaskStore(tmp_path / "tasks.db")
    events = []
    scope = RequestScope(allow)
    request = scope.begin_request(
        "Add report review tomorrow at 4 PM or 5 PM", source_id="typed:1"
    )
    assistant = Assistant(
        tasks=store,
        request_scope=scope,
        clock=lambda: datetime.fromisoformat("2026-11-09T18:00:00+05:30"),
        publish_event=events.append,
    )

    async def add():
        return await assistant.add_task(None, "Review", "2026-11-10T16:30:00Z")

    try:
        result = await RequestScopeTools(scope)._execute(
            _Binding(scope, request), "add_task", {"title": "Review"}, add
        )
        assert store.upcoming() == []
        assert events == []
        assert "not added" in result.lower()
    finally:
        store.close()
