"""Current-task admission over exported tools and inert external boundaries."""

from types import SimpleNamespace

import pytest
from livekit.agents import Agent, RunContext, ToolError, function_tool
from livekit.agents.voice.speech_handle import SpeechHandle

from agent import Assistant
from request_scope import ActionProposal, GeminiScopeJudge, RequestScope
from request_scope_livekit import RequestScopeTools
from skill_store import SkillStore


@pytest.fixture
def offline_provider(monkeypatch):
    """Construction is allowed; any unexpected provider request stays offline."""
    monkeypatch.setenv("GOOGLE_API_KEY", "offline-test-key")

    async def unavailable(**_):
        raise RuntimeError("Provider unavailable in this offline test")

    async def close():
        pass

    client = SimpleNamespace(
        aio=SimpleNamespace(
            models=SimpleNamespace(generate_content=unavailable), aclose=close
        ),
        close=lambda: None,
    )
    monkeypatch.setattr("google.genai.Client", lambda **_: client)
    return client


@pytest.mark.parametrize("scope_mode", ["default", "uninitialized"])
@pytest.mark.parametrize("effect", ["app", "navigation", "input", "click"])
async def test_microsoft_research_rejects_unrelated_exported_effects(
    tmp_path, monkeypatch, offline_provider, desktop_view, scope_mode, effect
):
    import json

    effects = []
    desktop, backend, context, _ = desktop_view
    backend.write = lambda text, **_: effects.append(("input", text))
    backend.click = lambda *args, **_: effects.append(("click", args))
    browser = SimpleNamespace()

    def navigate(url):
        effects.append(("navigation", url))
        return "opened"

    browser.navigate = navigate
    monkeypatch.setattr("agent._launch_via_start_menu", effects.append)
    scope = (
        RequestScope() if scope_mode == "default" else RequestScope(GeminiScopeJudge())
    )
    assistant = Assistant(
        browser=browser,
        desktop=desktop,
        skills=SkillStore(tmp_path),
        request_scope=scope,
    )
    request = scope.begin_request(
        "Do a deep search about Microsoft", source_id="research"
    )
    speech = SpeechHandle.create()
    assistant._scoped_tools.bind_speech(speech, request)
    context.speech_handle = speech
    frame = json.loads(
        await next(
            tool for tool in assistant.tools if tool.info.name == "capture_screen"
        )(context)
    )

    calls = {
        "app": ("open_app", ("Spotify",)),
        "navigation": ("browse_to", ("https://open.spotify.com",)),
        "input": ("type_on_desktop", ("play my old playlist", frame["observation_id"])),
        "click": ("click_mouse", (100, 100, frame["observation_id"])),
    }
    name, arguments = calls[effect]
    tool = next(tool for tool in assistant.tools if tool.info.name == name)
    with pytest.raises(ToolError):
        await tool(context, *arguments)
    assert effects == []
    # A refusal before host execution must not leave a reusable permit behind.
    assert all(item["tool"] == "capture_screen" for item in scope.history)
    await assistant._scoped_tools.aclose()


class AuthoringAssistant(Agent):
    def __init__(self, path):
        self.path = path
        super().__init__(instructions="Test skill authoring admission")

    @function_tool
    async def skill_manage(self, context: RunContext, action: str, name: str):
        self.path.write_text(f"{action}:{name}")
        return "saved"


@pytest.mark.parametrize("bound", [False, True])
async def test_skill_authoring_requires_current_task_permission(tmp_path, bound):
    path = tmp_path / "unrequested-skill.md"
    agent = AuthoringAssistant(path)
    scope = RequestScope()
    facade = RequestScopeTools(scope)
    tool = facade.wrap_tools(agent.tools)[0]
    context = None
    if bound:
        request = scope.begin_request(
            "Do a deep search about Microsoft", source_id="research"
        )
        speech = SpeechHandle.create()
        facade.bind_speech(speech, request)
        context = SimpleNamespace(speech_handle=speech)
    with pytest.raises(ToolError):
        await tool(context, "create", "play-spotify")
    assert not path.exists()
    await facade.aclose()


@pytest.mark.parametrize("scope_mode", ["default", "gemini", "denying-provider"])
async def test_exact_requested_effect_does_not_wait_for_provider_veto(
    monkeypatch, scope_mode
):
    attempts = []

    def unexpected_provider(**_):
        attempts.append("provider")
        raise AssertionError("An exact task does not need a provider decision")

    monkeypatch.setenv("GOOGLE_API_KEY", "offline-test-key")
    monkeypatch.setattr("google.genai.Client", unexpected_provider)

    async def deny(**_):
        attempts.append("provider")
        return SimpleNamespace(text='{"decision":"deny","reason":"Provider veto"}')

    client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=deny))
    )
    scope = (
        RequestScope()
        if scope_mode == "default"
        else RequestScope(
            GeminiScopeJudge(
                client=client if scope_mode == "denying-provider" else None
            )
        )
    )
    request = scope.begin_request("Open Notepad", source_id="explicit")
    permit = await scope.admit(
        request.id, ActionProposal("open_app", {"app_name": "Notepad"})
    )
    scope.consume(permit)
    scope.check(permit)
    assert attempts == []


async def test_default_checkpoints_renew_same_task_without_reviving_old_permits():
    now = [100.0]
    scope = RequestScope(clock=lambda: now[0])
    request = scope.begin_request("Open Notepad", source_id="long-task")
    proposal = ActionProposal("open_app", {"app_name": "Notepad"})
    old_permit = await scope.admit(request.id, proposal)
    for _ in range(64):
        permit = await scope.admit(request.id, proposal)
        scope.consume(permit)
        scope.record(permit, "done", "Notepad observed")
    now[0] = 1001.0
    permit = await scope.admit(request.id, proposal)
    scope.consume(permit)
    assert scope.current_request.id == request.id
    with pytest.raises(ToolError, match="expired"):
        scope.consume(old_permit)
    scope.stop()
    with pytest.raises(ToolError):
        await scope.admit(request.id, proposal)


async def test_unresolved_default_action_without_key_cannot_expand_scope(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    scope = RequestScope()
    request = scope.begin_request("Handle that", source_id="ambiguous")
    with pytest.raises(ToolError):
        await scope.admit(request.id, ActionProposal("click_mouse", {"x": 12, "y": 34}))


async def test_unresolved_judge_initializes_once_and_closes_owned_client(monkeypatch):
    events = []

    async def generate_content(**_):
        return SimpleNamespace(text='{"decision":"clarify","reason":"Which target?"}')

    async def async_close():
        events.append("async-close")

    client = SimpleNamespace(
        aio=SimpleNamespace(
            models=SimpleNamespace(generate_content=generate_content),
            aclose=async_close,
        ),
        close=lambda: events.append("close"),
    )

    def create_client(*, api_key):
        assert api_key == "offline-test-key"
        events.append("init")
        return client

    monkeypatch.setenv("GOOGLE_API_KEY", "offline-test-key")
    monkeypatch.setattr("google.genai.Client", create_client)
    scope = RequestScope()
    facade = RequestScopeTools(scope)
    request = scope.begin_request("Handle that", source_id="ambiguous")
    for _ in range(2):
        with pytest.raises(ToolError):
            await scope.admit(
                request.id, ActionProposal("click_mouse", {"x": 12, "y": 34})
            )
    await facade.aclose()
    await facade.aclose()
    assert events == ["init", "async-close", "close"]


async def test_settled_actions_publish_redacted_evidence_and_preserve_host_result(
    tmp_path,
):
    evidence = []

    async def record(request_id, name, outcome, metadata):
        evidence.append((request_id, name, outcome, metadata))
        raise RuntimeError("The optional history writer failed")

    async def judge(*_):
        from request_scope import AdmissionDecision

        return AdmissionDecision("allow", "Explicitly requested skill authoring")

    path = tmp_path / "requested-skill.md"
    agent = AuthoringAssistant(path)
    scope = RequestScope(judge)
    facade = RequestScopeTools(scope, on_action=record)
    request = scope.begin_request("Create my private procedure", source_id="author")
    speech = SpeechHandle.create()
    facade.bind_speech(speech, request)
    assert (
        await facade.wrap_tools(agent.tools)[0](
            SimpleNamespace(speech_handle=speech), "create", "private-procedure"
        )
        == "saved"
    )
    assert path.read_text() == "create:private-procedure"
    assert evidence == [
        (request.id, "skill_manage", "returned", {"argument_names": ["action", "name"]})
    ]
    assert "private-procedure" not in str(evidence)
    await facade.aclose()


async def test_completion_guard_blocks_explicit_and_automatic_unverified_finish():
    from livekit.agents import ChatMessage

    ready = [False]
    completions = []

    @function_tool
    async def finish_request(context: RunContext):
        return facade.finish_current_request()

    async def judge(*_):
        from request_scope import AdmissionDecision

        return AdmissionDecision("allow", "Outcome checked")

    scope = RequestScope(judge)
    facade = RequestScopeTools(
        scope,
        completion_check=lambda *_: ready[0],
        on_completion=lambda *args: completions.append(args),
    )
    request = scope.begin_request("Research Microsoft", source_id="research")
    speech = SpeechHandle.create()
    facade.bind_speech(speech, request)
    context = SimpleNamespace(speech_handle=speech)
    tool = facade.wrap_tools([finish_request])[0]
    with pytest.raises(ToolError, match="verified"):
        await tool(context)
    assert scope.require(request.id).id == request.id
    assert completions == []

    # A settled report goes through the same evidence gate.
    handle = SimpleNamespace(
        id="completed-report",
        interrupted=False,
        done=lambda: True,
        exception=lambda: None,
        chat_items=[
            ChatMessage(role="assistant", content=["Microsoft research is complete."])
        ],
    )
    facade._speeches[handle.id] = request
    await facade.review_completed_speech(handle)
    assert scope.require(request.id).id == request.id
    ready[0] = True
    assert await tool(context)
    assert completions == [(request.id, True, "Verified request completion")]
    await facade.aclose()


@pytest.mark.parametrize("close_event", [False, True])
async def test_session_close_preserves_settled_async_completion_evidence(close_event):
    import asyncio

    from test_request_scope_livekit import Session

    completions = []

    async def record(*args):
        await asyncio.sleep(0)
        if not scope.was_finished(args[0]):
            raise ToolError("Normal finish receipt required for history settlement")
        completions.append(args)

    scope = RequestScope()
    facade = RequestScopeTools(scope, on_completion=record)
    session = Session()
    facade.attach_session(session)
    request = scope.begin_request("Open Notepad", source_id="explicit")
    speech = SpeechHandle.create()
    facade.bind_speech(speech, request)

    @function_tool
    async def finish_request(context: RunContext):
        return facade.finish_current_request()

    await facade.wrap_tools([finish_request])[0](SimpleNamespace(speech_handle=speech))
    if close_event:
        session.emit("close", None)
    await facade.aclose()
    assert completions == [(request.id, True, "Verified request completion")]
    assert not scope.was_finished(request.id)


@pytest.mark.parametrize("change", ["stop", "new_request"])
async def test_shutdown_cannot_settle_superseded_or_stopped_request_as_success(change):
    import asyncio

    successful = []

    async def record(request_id, *_):
        await asyncio.sleep(0)
        if scope.was_finished(request_id):
            successful.append(request_id)

    scope = RequestScope()
    facade = RequestScopeTools(scope, on_completion=record)
    request = scope.begin_request("Open Notepad", source_id="old")
    speech = SpeechHandle.create()
    facade.bind_speech(speech, request)

    @function_tool
    async def finish_request(context: RunContext):
        return facade.finish_current_request()

    await facade.wrap_tools([finish_request])[0](SimpleNamespace(speech_handle=speech))
    if change == "stop":
        scope.stop()
    else:
        scope.begin_request("Open Calculator", source_id="new")
    await facade.aclose()
    assert successful == []
