"""Trusted-input and policy-outage boundaries, with inert host effects only."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from livekit.agents import AgentSession, RunContext, ToolError, function_tool
from livekit.agents.llm import FunctionCall, FunctionCallOutput, ToolContext
from livekit.agents.llm.utils import prepare_function_arguments
from livekit.agents.voice.events import (
    FunctionToolsExecutedEvent,
    SpeechCreatedEvent,
    UserInputTranscribedEvent,
)
from livekit.agents.voice.speech_handle import SpeechHandle
from test_request_scope_livekit import Session, setup_facade

from request_scope import (
    ActionProposal,
    AdmissionDecision,
    GeminiScopeJudge,
    RequestScope,
)
from request_scope_livekit import RequestScopeTools
from tool_dispatch import repair_function_tool


def voice(facade, session, source):
    session._activity._rt_session._current_generation = SimpleNamespace(input_id=source)
    handle = SpeechHandle.create()
    session.emit(
        "speech_created",
        SpeechCreatedEvent(
            speech_handle=handle, user_initiated=False, source="generate_reply"
        ),
    )
    return SimpleNamespace(
        speech_handle=handle, function_call=SimpleNamespace(call_id=handle.id)
    )


def transcript(session, source, text="Open Notepad", *, final=True):
    session.emit(
        "user_input_transcribed",
        UserInputTranscribedEvent(transcript=text, is_final=final, item_id=source),
    )


async def test_matching_final_releases_one_waiting_effect_without_inferred_request():
    effects = []
    agent, scope, facade, _ = setup_facade(effects)
    session = Session()
    facade.attach_session(session)
    ctx = voice(facade, session, "matching")
    task = asyncio.create_task(agent.tools[0](ctx, "Notepad"))
    await asyncio.sleep(0.01)
    assert effects == []
    assert scope.current_request is None
    transcript(session, "matching", final=False)
    await asyncio.sleep(0.01)
    assert effects == []
    assert scope.current_request is None
    transcript(session, "matching")
    await asyncio.wait_for(task, 0.5)
    assert effects == ["Notepad"]
    assert scope.current_request.text == "Open Notepad"
    await facade.aclose()


@pytest.mark.parametrize("invalidator", ["new_final", "new_partial", "stop", "close"])
async def test_pending_voice_wait_is_invalidated_before_late_matching_final(
    invalidator,
):
    effects = []
    agent, _, facade, _ = setup_facade(effects)
    session = Session()
    facade.attach_session(session)
    ctx = voice(facade, session, "old")
    task = asyncio.create_task(agent.tools[0](ctx, "Notepad"))
    await asyncio.sleep(0.01)
    if invalidator == "close":
        session.emit("close", None)
    elif invalidator == "stop":
        await facade.stop_current_request()
    else:
        transcript(
            session,
            "new",
            "Actually do not open that",
            final=invalidator == "new_final",
        )
    transcript(session, "old")
    with pytest.raises(ToolError):
        await asyncio.wait_for(task, 0.5)
    assert effects == []
    await facade.aclose()


async def test_missing_matching_final_returns_bounded_structured_failure():
    effects = []
    agent, scope, facade, _ = setup_facade(effects)
    facade.final_transcript_timeout = 0.02
    session = Session()
    facade.attach_session(session)
    with pytest.raises(ToolError) as error:
        await asyncio.wait_for(
            agent.tools[0](voice(facade, session, "missing"), "Notepad"), 0.3
        )
    payload = json.loads(str(error.value))
    assert payload["status"] == "awaiting_final_input"
    assert payload["retryable"] is False
    assert effects == []
    assert scope.current_request is None
    await facade.aclose()


async def test_sdk_result_event_binds_continuation_to_its_original_request():
    effects = []
    agent, scope, facade, _ = setup_facade(effects)
    session = Session()
    facade.attach_session(session)
    first = voice(facade, session, "human-1")
    transcript(session, "human-1")
    await agent.tools[0](first, "Notepad")
    original = scope.current_request
    session.emit(
        "function_tools_executed",
        FunctionToolsExecutedEvent(
            function_calls=[
                FunctionCall(
                    name="open_app",
                    call_id=first.function_call.call_id,
                    arguments='{"app_name":"Notepad"}',
                )
            ],
            function_call_outputs=[
                FunctionCallOutput(
                    name="open_app",
                    call_id=first.function_call.call_id,
                    output="opened",
                    is_error=False,
                )
            ],
        ),
    )
    await agent.tools[0](voice(facade, session, "tool-result"), "Notepad")
    assert effects == ["Notepad", "Notepad"]
    assert scope.current_request is original
    await facade.aclose()


async def test_unrelated_generation_cannot_use_unpublished_tool_result():
    effects = []
    agent, _, facade, _ = setup_facade(effects)
    facade.final_transcript_timeout = 0.01
    session = Session()
    facade.attach_session(session)
    first = voice(facade, session, "human-1")
    transcript(session, "human-1")
    await agent.tools[0](first, "Notepad")
    with pytest.raises(ToolError):
        await agent.tools[0](voice(facade, session, "unrelated"), "Notepad")
    assert effects == ["Notepad"]
    await facade.aclose()


def unavailable_client(handler=None):
    async def quota(**kwargs):
        raise RuntimeError("429 RESOURCE_EXHAUSTED private-provider-content")

    return SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=handler or quota))
    )


@pytest.mark.parametrize(
    ("text", "action"),
    [
        ("Describe the screen only", ActionProposal("desktop_type", {"text": "hello"})),
        ("Describe the screen only", ActionProposal("click_mouse", {"x": 1, "y": 2})),
        (
            "Describe the screen only",
            ActionProposal("browse_to", {"url": "https://example.com"}),
        ),
        ("Open Notepad", ActionProposal("open_app", {"app_name": "Calculator"})),
        ("Do not open Notepad", ActionProposal("open_app", {"app_name": "Notepad"})),
        ("Describe Notepad", ActionProposal("open_app", {"app_name": "Notepad"})),
        (
            "Open https://example.com",
            ActionProposal("browse_to", {"url": "https://example.com.evil.test"}),
        ),
        (
            "Open Notepad",
            ActionProposal(
                "complete_request", {"assistant_report": "done"}, "orchestrate"
            ),
        ),
        (
            "Open Notepad",
            ActionProposal("run_skill", {"name": "send message"}, "orchestrate"),
        ),
    ],
)
async def test_quota_cannot_expand_or_invent_the_requested_effect(text, action):
    scope = RequestScope(GeminiScopeJudge(client=unavailable_client()))
    request = scope.begin_request(text, source_id="human:1")
    with pytest.raises(ToolError) as error:
        await scope.admit(request.id, action)
    payload = json.loads(str(error.value))
    assert payload["status"] == "policy_unavailable"
    assert payload["retryable"] is True
    assert "confirm" not in payload["instruction"].lower()


@pytest.mark.parametrize(
    ("text", "action"),
    [
        ("Open Notepad", ActionProposal("open_app", {"app_name": "Notepad"})),
        (
            "Please launch Calculator.",
            ActionProposal("open_app", {"app_name": "Calculator"}),
        ),
        (
            "Open https://example.com/path?q=hello",
            ActionProposal("browse_to", {"url": "https://example.com/path?q=hello"}),
        ),
        ("Describe the screen only", ActionProposal("capture_screen", kind="observe")),
    ],
)
async def test_quota_preserves_exact_simple_commands(text, action):
    scope = RequestScope(GeminiScopeJudge(client=unavailable_client()))
    request = scope.begin_request(text, source_id="human:1")
    permit = await scope.admit(request.id, action)
    scope.consume(permit)
    scope.check(permit)


async def test_policy_fallback_attempts_fit_the_overall_deadline():
    attempts = []

    async def stalled(*, model, **kwargs):
        attempts.append(model)
        await asyncio.Event().wait()

    judge = GeminiScopeJudge(
        client=unavailable_client(stalled), model="gemini-3.5-flash-lite", timeout=0.3
    )
    scope = RequestScope(judge, policy_timeout=0.6)
    request = scope.begin_request("Open Notepad", source_id="human:1")
    permit = await asyncio.wait_for(
        scope.admit(request.id, ActionProposal("open_app", {"app_name": "Notepad"})),
        1.0,
    )
    scope.consume(permit)
    assert len(attempts) == 4


async def test_scope_logs_correlate_permit_without_raw_user_or_provider_content(caplog):
    caplog.set_level("INFO", logger="request_scope")
    scope = RequestScope(GeminiScopeJudge(client=unavailable_client()))
    request = scope.begin_request("Open SecretApp", source_id="voice:source-1")
    permit = await scope.admit(
        request.id, ActionProposal("open_app", {"app_name": "SecretApp"})
    )
    scope.consume(permit)
    scope.record(permit, "done", "private-result")
    assert request.id in caplog.text
    assert permit.id in caplog.text
    assert "SecretApp" not in caplog.text
    assert "private-result" not in caplog.text
    assert "private-provider-content" not in caplog.text


async def test_new_finalized_request_after_interim_stop_survives_stop_callback():
    effects = []
    agent, _, facade, _ = setup_facade(effects)
    session = Session()
    facade.attach_session(session)
    voice(facade, session, "stop")
    transcript(session, "stop", "stop", final=False)
    fresh = voice(facade, session, "fresh")
    transcript(session, "fresh")
    await asyncio.sleep(0)
    await agent.tools[0](fresh, "Notepad")
    assert effects == ["Notepad"]
    await facade.aclose()


async def test_quota_navigation_preserves_case_sensitive_path():
    scope = RequestScope(GeminiScopeJudge(client=unavailable_client()))
    request = scope.begin_request(
        "Open https://example.com/private", source_id="human:1"
    )
    with pytest.raises(ToolError):
        await scope.admit(
            request.id,
            ActionProposal("browse_to", {"url": "https://example.com/PRIVATE"}),
        )


async def test_scope_revocation_cannot_be_reopened_by_late_final_on_a_new_handle():
    effects = []
    agent, scope, facade, _ = setup_facade(effects)
    facade.final_transcript_timeout = 0.01
    session = Session()
    facade.attach_session(session)
    voice(facade, session, "old")
    scope.stop()
    transcript(session, "old")
    with pytest.raises(ToolError):
        await agent.tools[0](voice(facade, session, "old"), "Notepad")
    assert effects == []
    await facade.aclose()


async def test_duplicate_final_answer_keeps_the_explicit_clarification_identity():
    scope = RequestScope()
    original = scope.begin_request("Write a note", source_id="human:1")
    incoming = scope.begin_request("hello", source_id="human:2")
    continued = scope.continue_with_answer(original, incoming.id)
    duplicate = scope.begin_request("hello", source_id="human:2")
    assert duplicate is continued
    assert scope.current_request is continued


def publish_result(session, context, name, *, is_error=False):
    call_id = context.function_call.call_id
    session.emit(
        "function_tools_executed",
        FunctionToolsExecutedEvent(
            function_calls=[FunctionCall(name=name, call_id=call_id, arguments="{}")],
            function_call_outputs=[
                FunctionCallOutput(
                    name=name,
                    call_id=call_id,
                    output="fixture result",
                    is_error=is_error,
                )
            ],
        ),
    )


@pytest.mark.parametrize("read_name", ["view_skill", "list_tasks"])
async def test_bound_local_read_result_preserves_voice_request_for_next_effect(
    read_name,
):
    effects = []
    agent, scope, facade, _ = setup_facade(effects)
    facade.final_transcript_timeout = 0.01
    session = Session()
    facade.attach_session(session)

    @function_tool(name=read_name)
    async def read(context: RunContext):
        return "local information"

    read_tool = facade.wrap_tools([read])[0]
    first = voice(facade, session, "human-read")
    transcript(session, "human-read")
    original = scope.current_request
    assert await read_tool(first) == "local information"
    publish_result(session, first, read_name)
    await agent.tools[0](voice(facade, session, "read-result"), "Notepad")
    assert effects == ["Notepad"]
    assert scope.current_request is original
    await facade.aclose()


async def test_bound_transient_policy_failure_result_preserves_safe_retry():
    effects = []
    agent, scope, facade, _ = setup_facade(effects)
    facade.final_transcript_timeout = 0.01
    attempts = 0

    async def transient_policy(request, action, history):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("simulated temporary outage")
        return AdmissionDecision("allow", "Requested Notepad launch")

    scope.judge = transient_policy
    session = Session()
    facade.attach_session(session)
    first = voice(facade, session, "human-retry")
    transcript(session, "human-retry")
    original = scope.current_request
    with pytest.raises(ToolError) as error:
        await agent.tools[0](first, "Notepad")
    assert json.loads(str(error.value))["retryable"] is True
    assert effects == []
    publish_result(session, first, "open_app", is_error=True)
    await agent.tools[0](voice(facade, session, "failure-result"), "Notepad")
    assert effects == ["Notepad"]
    assert scope.current_request is original
    await facade.aclose()


async def test_bound_operation_failure_continuation_cannot_replay_uncertain_effect():
    effects, attempted = [], []

    async def uncertain_operation():
        attempted.append("started")
        raise RuntimeError("simulated lost acknowledgement")

    agent, _, facade, _ = setup_facade(effects, uncertain_operation)
    facade.final_transcript_timeout = 0.01
    session = Session()
    facade.attach_session(session)
    first = voice(facade, session, "human-uncertain")
    transcript(session, "human-uncertain")
    with pytest.raises(RuntimeError):
        await agent.tools[0](first, "Notepad")
    publish_result(session, first, "open_app", is_error=True)
    with pytest.raises(ToolError, match="uncertain"):
        await agent.tools[0](voice(facade, session, "uncertain-result"), "Notepad")
    assert attempted == ["started"]
    assert effects == []
    await facade.aclose()


@pytest.mark.parametrize("change", ["stop", "new_input", "interrupt"])
async def test_bound_local_read_result_cannot_revive_invalidated_origin(change):
    effects = []
    agent, _, facade, _ = setup_facade(effects)
    facade.final_transcript_timeout = 0.01
    session = Session()
    facade.attach_session(session)

    @function_tool
    async def list_tasks(context: RunContext):
        return "local information"

    read = facade.wrap_tools([list_tasks])[0]
    first = voice(facade, session, "human-read")
    transcript(session, "human-read")
    await read(first)
    if change == "stop":
        await facade.stop_current_request()
    elif change == "new_input":
        transcript(session, "new-human", "Describe the screen")
    else:
        first.speech_handle.interrupt()
    publish_result(session, first, "list_tasks")
    with pytest.raises(ToolError):
        await agent.tools[0](voice(facade, session, "stale-read-result"), "Notepad")
    assert effects == []
    await facade.aclose()


async def test_scoped_provider_and_direct_alias_calls_preserve_dictionary_input_schema():
    seen = []
    _, _, facade, _ = setup_facade([])
    session = Session()
    facade.attach_session(session)

    @function_tool
    async def run_skill(context: RunContext, name: str, inputs_json: dict[str, int]):
        seen.append((name, inputs_json))
        return "done"

    tool = facade.wrap_tools([run_skill])[0]
    ctx = voice(facade, session, "human-dict")
    transcript(session, "human-dict")
    ctx = RunContext(
        session=SimpleNamespace(_global_run_state=None),
        speech_handle=ctx.speech_handle,
        function_call=FunctionCall(
            name="run_skill", call_id="dict-call", arguments="{}"
        ),
    )
    args, kwargs = prepare_function_arguments(
        fnc=tool, json_arguments='{"skill":"sample","inputs":{"x":2}}', call_ctx=ctx
    )
    await tool(*args, **kwargs)
    await tool(ctx, skill="sample", inputs={"x": 3})
    assert seen == [("sample", {"x": 2}), ("sample", {"x": 3})]
    await facade.aclose()


@pytest.mark.parametrize("already_scoped", [False, True])
async def test_scope_can_wrap_repaired_tool_without_losing_validation_or_outer_guard(
    already_scoped,
):
    effects = []
    agent, inner_scope, inner, original = setup_facade(effects)
    supplied = agent.tools[0] if already_scoped else repair_function_tool(original)
    outer_scope = RequestScope(inner_scope.judge)
    outer = RequestScopeTools(outer_scope)
    tool = outer.wrap_tools([supplied])[0]
    assert outer.wrap_tools([tool])[0] is tool
    for json_schema in (False, True):
        assert ToolContext([tool]).parse_function_tools(
            "google", use_parameters_json_schema=json_schema
        ) == ToolContext([original]).parse_function_tools(
            "google", use_parameters_json_schema=json_schema
        )
    assert tool.info.flags == original.info.flags
    assert tool.info.on_duplicate == original.info.on_duplicate
    assert tool.info.duplicate_scope == original.info.duplicate_scope
    ctx = RunContext(
        session=AgentSession(),
        speech_handle=SpeechHandle.create(),
        function_call=FunctionCall(
            name="open_app", call_id="rewrapped", arguments="{}"
        ),
    )
    inner.bind_speech(
        ctx.speech_handle,
        inner_scope.begin_request("Open Notepad", source_id="inner:1"),
    )
    outer.bind_speech(
        ctx.speech_handle,
        outer_scope.begin_request("Open Notepad", source_id="outer:1"),
    )
    args, kwargs = prepare_function_arguments(
        fnc=tool, json_arguments='{"name":"Notepad"}', call_ctx=ctx
    )
    assert await tool(*args, **kwargs) == "opened"
    assert await tool(ctx, app="Notepad") == "opened"
    with pytest.raises(ToolError):
        args, kwargs = prepare_function_arguments(
            fnc=tool, json_arguments='{"name":42}', call_ctx=ctx
        )
        await tool(*args, **kwargs)
    assert effects == ["Notepad", "Notepad"]
    outer_scope.stop()
    with pytest.raises(ToolError):
        await tool(ctx, "Notepad")
    assert effects == ["Notepad", "Notepad"]
    await outer.aclose()
    await inner.aclose()


async def test_rewrapped_effect_rechecks_outer_permit_at_native_boundary():
    effects = []
    entered, release = asyncio.Event(), asyncio.Event()

    async def before_effect():
        entered.set()
        await release.wait()

    agent, inner_scope, inner, _ = setup_facade(effects, before_effect)
    outer_scope = RequestScope(inner_scope.judge)
    outer = RequestScopeTools(outer_scope)
    tool = outer.wrap_tools(agent.tools)[0]
    ctx = RunContext(
        session=AgentSession(),
        speech_handle=SpeechHandle.create(),
        function_call=FunctionCall(
            name="open_app", call_id="rewrapped-native", arguments="{}"
        ),
    )
    inner.bind_speech(
        ctx.speech_handle,
        inner_scope.begin_request("Open Notepad", source_id="inner:1"),
    )
    outer.bind_speech(
        ctx.speech_handle,
        outer_scope.begin_request("Open Notepad", source_id="outer:1"),
    )
    task = asyncio.create_task(tool(ctx, "Notepad"))
    await asyncio.wait_for(entered.wait(), 0.5)
    outer_scope.stop()
    release.set()
    with pytest.raises(ToolError):
        await task
    assert effects == []
    await outer.aclose()
    await inner.aclose()
