import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from livekit.agents import Agent, RunContext, ToolError, function_tool
from livekit.agents.llm import ToolContext, ToolFlag
from livekit.agents.voice.events import SpeechCreatedEvent, UserInputTranscribedEvent
from livekit.agents.voice.speech_handle import SpeechHandle

from request_scope import AdmissionDecision, RequestScope
from request_scope_livekit import RequestScopeTools, check_current_action
from skill_execution import ToolOutcome, ToolRegistry


class Judge:
    async def __call__(self, request, action, history):
        allowed = (
            request.text.startswith("Open Notepad")
            and action.arguments.get("app_name", "Notepad") == "Notepad"
        )
        return AdmissionDecision(
            "allow" if allowed else "clarify", "Ask for the intended application"
        )


class ExampleAssistant(Agent):
    def __init__(self, effects, before_effect=None):
        self.effects = effects
        self.before_effect = before_effect
        super().__init__(instructions="Test harmless tool facade")

    @function_tool(flags=ToolFlag.CANCELLABLE, on_duplicate="reject")
    async def open_app(self, context: RunContext, app_name: str) -> str:
        if self.before_effect:
            await self.before_effect()
        await asyncio.to_thread(check_current_action)
        self.effects.append(app_name)
        return "opened"


def setup_facade(effects, before_effect=None):
    agent = ExampleAssistant(effects, before_effect)
    scope = RequestScope(Judge())
    facade = RequestScopeTools(scope)
    original = agent.tools[0]
    agent._tools = facade.wrap_tools(agent.tools)
    return agent, scope, facade, original


def context(facade, request):
    speech = SpeechHandle.create()
    facade.bind_speech(speech, request)
    return SimpleNamespace(
        speech_handle=speech, function_call=SimpleNamespace(call_id="call")
    )


async def test_exported_facade_preserves_schema_and_rejects_effect_without_human_input():
    effects = []
    agent, scope, facade, original = setup_facade(effects)
    wrapped = agent.tools[0]
    for json_schema in (False, True):
        assert ToolContext([wrapped]).parse_function_tools(
            "google", use_parameters_json_schema=json_schema
        ) == ToolContext([original]).parse_function_tools(
            "google", use_parameters_json_schema=json_schema
        )
    assert wrapped.info.flags == original.info.flags
    assert wrapped.info.on_duplicate == original.info.on_duplicate
    assert wrapped.info.duplicate_scope == original.info.duplicate_scope
    assert facade.wrap_tools([wrapped])[0] is wrapped
    with pytest.raises(ToolError):
        await wrapped(None, "Notepad")
    assert effects == []
    request = scope.begin_request("Open Notepad", source_id="human:1")
    await wrapped(context(facade, request), "Notepad")
    assert effects == ["Notepad"]


async def test_queued_exported_call_does_not_adopt_new_request():
    effects = []
    entered = asyncio.Event()
    release = asyncio.Event()

    async def before_effect():
        entered.set()
        await release.wait()

    agent, scope, facade, _ = setup_facade(effects, before_effect)
    first = scope.begin_request("Open Notepad", source_id="human:1")
    old_context = context(facade, first)
    job = asyncio.create_task(agent.tools[0](old_context, "Notepad"))
    await entered.wait()
    scope.begin_request("Open Calculator", source_id="human:2")
    release.set()
    with pytest.raises(ToolError):
        await job
    with pytest.raises(ToolError):
        await agent.tools[0](old_context, "Notepad")
    assert effects == []


async def test_interrupt_while_exported_effect_waits_prevents_native_effect():
    effects = []
    entered = asyncio.Event()
    release = asyncio.Event()

    async def before_effect():
        entered.set()
        await release.wait()

    agent, scope, facade, _ = setup_facade(effects, before_effect)
    request = scope.begin_request("Open Notepad", source_id="human:1")
    ctx = context(facade, request)
    job = asyncio.create_task(agent.tools[0](ctx, "Notepad"))
    await entered.wait()
    ctx.speech_handle.interrupt()
    release.set()
    with pytest.raises(ToolError):
        await job
    assert effects == []


class Session:
    def __init__(self):
        self.callbacks = {}
        self._activity = SimpleNamespace(
            _rt_session=SimpleNamespace(_current_generation=None)
        )

    def on(self, event, callback):
        self.callbacks.setdefault(event, []).append(callback)

    def off(self, event, callback):
        self.callbacks[event].remove(callback)

    def emit(self, event, value):
        for callback in self.callbacks.get(event, []):
            callback(value)


async def test_final_voice_transcript_binds_its_speech_and_images_never_admit_requests():
    effects = []
    agent, scope, facade, _ = setup_facade(effects)
    session = Session()
    facade.attach_session(session)
    session.emit(
        "conversation_item_added",
        SimpleNamespace(
            item=SimpleNamespace(role="user", content=["Open Notepad", object()])
        ),
    )
    assert scope.current_request is None
    handle = SpeechHandle.create()
    session._activity._rt_session._current_generation = SimpleNamespace(
        input_id="voice-1"
    )
    session.emit(
        "speech_created",
        SpeechCreatedEvent(
            speech_handle=handle, user_initiated=False, source="generate_reply"
        ),
    )
    session.emit(
        "user_input_transcribed",
        UserInputTranscribedEvent(
            transcript="Open Notepad", is_final=True, item_id="voice-1"
        ),
    )
    ctx = SimpleNamespace(speech_handle=handle)
    await agent.tools[0](ctx, "Notepad")
    assert effects == ["Notepad"]
    await facade.stop_current_request()
    session.emit(
        "user_input_transcribed",
        UserInputTranscribedEvent(
            transcript="Open Notepad", is_final=True, item_id="voice-1"
        ),
    )
    with pytest.raises(ToolError):
        await agent.tools[0](ctx, "Notepad")
    assert effects == ["Notepad"]


async def test_each_saved_registry_effect_is_gated_in_inherited_task_context():
    effects = []
    scope = RequestScope(Judge())
    facade = RequestScopeTools(scope)

    def launch(app_name: str):
        check_current_action()
        effects.append(app_name)
        return ToolOutcome(True, "opened")

    registry = facade.wrap_registry(ToolRegistry({"open_app": launch}))

    @function_tool
    async def run_skill(context: RunContext, name: str):
        async def engine_task():
            await registry.call("open_app", {"app_name": "Notepad"})
            await registry.call("open_app", {"app_name": "Calculator"})

        return await asyncio.create_task(engine_task())

    wrapped = facade.wrap_tools([run_skill])[0]
    request = scope.begin_request("Open Notepad", source_id="human:1")
    with pytest.raises(ToolError):
        await wrapped(context(facade, request), "saved procedure")
    assert effects == ["Notepad"]


async def test_tool_result_continuation_cannot_adopt_a_new_human_request():
    effects = []
    agent, scope, facade, _ = setup_facade(effects)
    session = Session()
    facade.attach_session(session)
    original = scope.begin_request("Open Notepad", source_id="human:1")
    await agent.tools[0](context(facade, original), "Notepad")
    scope.begin_request("Open Notepad again", source_id="human:2")
    continuation = SpeechHandle.create()
    session._activity._rt_session._current_generation = SimpleNamespace(
        input_id="tool-reply"
    )
    session.emit(
        "speech_created",
        SpeechCreatedEvent(
            speech_handle=continuation, user_initiated=False, source="generate_reply"
        ),
    )
    with pytest.raises(ToolError):
        await agent.tools[0](SimpleNamespace(speech_handle=continuation), "Notepad")
    assert effects == ["Notepad"]


async def test_typed_request_is_bound_before_generated_tools_start():
    effects = []
    agent, scope, facade, _ = setup_facade(effects)

    class TextSession(Session):
        @asynccontextmanager
        async def _claim_user_turn(self):
            yield

        async def interrupt(self):
            pass

        def generate_reply(self, user_input):
            handle = SpeechHandle.create()
            self.emit(
                "speech_created",
                SpeechCreatedEvent(
                    speech_handle=handle, user_initiated=True, source="generate_reply"
                ),
            )
            self.tool_task = asyncio.create_task(
                agent.tools[0](SimpleNamespace(speech_handle=handle), "Notepad")
            )
            return handle

    session = TextSession()
    await facade.text_input_cb(
        session,
        SimpleNamespace(text="Open Notepad", info=SimpleNamespace(stream_id="typed-1")),
    )
    await session.tool_task
    assert effects == ["Notepad"]
    facade.attach_session(Session())
    with pytest.raises(ToolError):
        await agent.tools[0](context(facade, scope.current_request), "Notepad")


async def test_saved_skill_answer_renews_only_waiting_worker_and_rejects_replay():
    effects = []
    resume = asyncio.Event()
    worker = None

    async def judge(request, action, history):
        allowed = action.name != "answer_skill" or (
            request.text == "hello" and action.arguments["answer"] == "hello"
        )
        return AdmissionDecision(
            "allow" if allowed else "clarify", "Answer must come from the user"
        )

    scope = RequestScope(judge)
    facade = RequestScopeTools(scope)

    def type_text(text: str):
        check_current_action()
        effects.append(text)
        return ToolOutcome(True, "typed")

    registry = facade.wrap_registry(ToolRegistry({"desktop_type": type_text}))

    @function_tool
    async def run_skill(context: RunContext, name: str):
        nonlocal worker

        async def engine():
            await resume.wait()
            return await registry.call("desktop_type", {"text": "hello"})

        worker = asyncio.create_task(engine())
        return json.dumps(
            {
                "status": "waiting",
                "run_id": "run-1",
                "question_id": "q-1",
                "question": "What should I type?",
            }
        )

    @function_tool
    async def answer_skill(
        context: RunContext, answer: str, run_id: str, question_id: str
    ):
        resume.set()
        await worker
        return "done"

    run, answer = facade.wrap_tools([run_skill, answer_skill])
    original = scope.begin_request(
        "Open Notepad and ask what to type", source_id="human:1"
    )
    old_ctx = context(facade, original)
    await run(old_ctx, "write note")
    incoming = scope.begin_request("Open Calculator", source_id="human:2")
    with pytest.raises(ToolError):
        await answer(context(facade, incoming), "hello", "run-1", "q-1")
    assert effects == []
    incoming = scope.begin_request("hello", source_id="human:3")
    await answer(context(facade, incoming), "hello", "run-1", "q-1")
    assert effects == ["hello"]
    incoming = scope.begin_request("hello", source_id="human:4")
    with pytest.raises(ToolError):
        await answer(context(facade, incoming), "hello", "run-1", "q-1")
    assert effects == ["hello"]


async def test_interrupted_speech_and_interim_human_input_suspend_effects():
    effects = []
    agent, scope, facade, _ = setup_facade(effects)
    session = Session()
    facade.attach_session(session)
    request = scope.begin_request("Open Notepad", source_id="human:1")
    ctx = context(facade, request)
    ctx.speech_handle.interrupt()
    with pytest.raises(ToolError):
        await agent.tools[0](ctx, "Notepad")
    request = scope.begin_request("Open Notepad", source_id="human:2")
    ctx = context(facade, request)
    session.emit(
        "user_input_transcribed",
        UserInputTranscribedEvent(
            transcript="stop", is_final=False, item_id="voice-stop"
        ),
    )
    with pytest.raises(ToolError):
        await agent.tools[0](ctx, "Notepad")
    assert effects == []


async def test_google_tool_queue_gets_final_transcript_through_fallback_before_effect(
    monkeypatch,
):
    """Characterize the installed SDK ordering this authority binding relies on."""
    from google.genai import types
    from livekit.plugins.google.realtime.realtime_api import (
        RealtimeModel,
        RealtimeSession,
    )

    from realtime_model import GeminiRealtimeFallback

    async def no_network(self):
        return

    monkeypatch.setattr(RealtimeSession, "_main_task", no_network)
    model = RealtimeModel(
        model="gemini-2.5-flash-native-audio-latest", api_key="test-only"
    )
    fallback = GeminiRealtimeFallback([model]).session()
    provider = fallback._active
    effects = []
    agent, _scope, facade, _ = setup_facade(effects)
    session = Session()
    session._activity._rt_session = fallback
    facade.attach_session(session)
    ctx = None

    def generation_created(event):
        nonlocal ctx
        speech = SpeechHandle.create()
        ctx = SimpleNamespace(speech_handle=speech)
        session.emit(
            "speech_created",
            SpeechCreatedEvent(
                speech_handle=speech,
                user_initiated=event.user_initiated,
                source="generate_reply",
            ),
        )

    def transcribed(event):
        session.emit(
            "user_input_transcribed",
            UserInputTranscribedEvent(
                transcript=event.transcript,
                is_final=event.is_final,
                item_id=event.item_id,
            ),
        )

    fallback.on("generation_created", generation_created)
    fallback.on("input_audio_transcription_completed", transcribed)
    try:
        provider._start_new_generation()
        generation = provider._current_generation
        generation.input_transcription = "Open Notepad"

        async def consume_calls():
            async for call in generation.function_ch:
                await agent.tools[0](ctx, **json.loads(call.arguments))

        job = asyncio.create_task(consume_calls())
        provider._handle_tool_calls(
            types.LiveServerToolCall(
                function_calls=[
                    types.FunctionCall(
                        id="call-1", name="open_app", args={"app_name": "Notepad"}
                    )
                ]
            )
        )
        await job
        assert effects == ["Notepad"]
    finally:
        await fallback.aclose()


async def test_direct_clarification_keeps_task_but_unrelated_new_request_is_independent():
    effects = []

    async def judge(request, action, history):
        if action.name == "answer_request":
            allowed = request.text == "the top one"
        elif request.text == "Select the Kommidi profile":
            allowed = (
                request.answers == ("the top one",)
                and action.arguments["app_name"] == "Kommidi top"
            )
        else:
            allowed = (
                request.text == "Open Calculator"
                and action.arguments["app_name"] == "Calculator"
            )
        return AdmissionDecision(
            "allow" if allowed else "deny", "Needs a matching user request"
        )

    scope = RequestScope(judge)
    facade = RequestScopeTools(scope)
    agent = ExampleAssistant(effects)

    @function_tool
    async def clarify_request(context: RunContext, question: str):
        return facade.clarify_request(question)

    launch, clarify = facade.wrap_tools([agent.tools[0], clarify_request])
    request = scope.begin_request("Select the Kommidi profile", source_id="human:1")
    ctx = context(facade, request)
    await clarify(ctx, "Which Kommidi profile, top or bottom?")
    with pytest.raises(ToolError):
        await launch(ctx, "Kommidi top")
    answer = scope.begin_request("the top one", source_id="human:2")
    await launch(context(facade, answer), "Kommidi top")
    assert effects == ["Kommidi top"]

    request = scope.begin_request("Select the Kommidi profile", source_id="human:3")
    await clarify(context(facade, request), "Top or bottom?")
    independent = scope.begin_request("Open Calculator", source_id="human:4")
    await launch(context(facade, independent), "Calculator")
    assert effects == ["Kommidi top", "Calculator"]


async def test_registry_selection_is_internal_only_until_exported_run_binds_authority():
    effects = []
    scope = RequestScope(Judge())
    facade = RequestScopeTools(scope)

    def launch(app_name: str):
        effects.append(app_name)
        return ToolOutcome(True, "opened")

    registry = ToolRegistry({"open_app": launch})
    await facade.registry_for_current_request(registry).call(
        "open_app", {"app_name": "internal"}
    )

    @function_tool
    async def run_skill(context: RunContext, name: str):
        selected = facade.registry_for_current_request(registry)
        await selected.call("open_app", {"app_name": "Calculator"})

    wrapped = facade.wrap_tools([run_skill])[0]
    with pytest.raises(ToolError):
        await wrapped(None, "procedure")
    request = scope.begin_request("Open Notepad", source_id="human:1")
    with pytest.raises(ToolError):
        await wrapped(context(facade, request), "procedure")
    assert effects == ["internal"]


async def test_closed_facade_detaches_human_input_and_revokes_existing_tools():
    effects = []
    agent, scope, facade, _ = setup_facade(effects)
    session = Session()
    facade.attach_session(session)
    request = scope.begin_request("Open Notepad", source_id="human:1")
    await facade.aclose()
    session.emit(
        "user_input_transcribed",
        UserInputTranscribedEvent(
            transcript="Open Notepad", is_final=True, item_id="new-voice"
        ),
    )
    with pytest.raises(ToolError):
        await agent.tools[0](context(facade, request), "Notepad")
    assert effects == []


async def test_real_skill_invalid_answer_can_be_corrected_without_losing_scope(
    tmp_path,
):
    from dataclasses import asdict

    from skill_execution import CheckOutcome, SkillExecutionService
    from skill_format import Action, Check, Skill, SkillInput, Step
    from skill_runs import SkillRunStore
    from telemetry import SessionStatusReporter

    effects = []

    async def judge(*args):
        return AdmissionDecision("allow", "User answers the pending integer question")

    async def write(value: int):
        check_current_action()
        effects.append(value)
        return ToolOutcome(True, "written")

    scope = RequestScope(judge)
    facade = RequestScopeTools(scope)
    registry = ToolRegistry({"write": write})
    service = SkillExecutionService(
        verifier=lambda check: CheckOutcome(True, "observed"),
        run_store=SkillRunStore(tmp_path / "scope-runs.db"),
    )
    skill = Skill(
        "Integer entry",
        "Ask for an integer then write it",
        (
            Step(1, "Ask", action=Action("ask", param="count", text="Which integer?")),
            Step(
                2,
                "Write",
                action=Action("tool", tool="write", args=(("value", "{count}"),)),
                check=Check("page_contains", "ready"),
            ),
        ),
        inputs=(SkillInput("count", "count"),),
        allowed_tools=("write",),
        state="enabled",
    )

    @function_tool
    async def run_skill(context: RunContext, name: str):
        result = await service.run(
            skill,
            facade.registry_for_current_request(registry),
            SessionStatusReporter(sink=lambda _: None),
            run_id="integer-run",
        )
        return json.dumps(asdict(result))

    @function_tool
    async def answer_skill(
        context: RunContext, answer: str, run_id: str, question_id: str
    ):
        return json.dumps(
            asdict(await service.answer(run_id, answer, question_id=question_id))
        )

    run, answer = facade.wrap_tools([run_skill, answer_skill])
    try:
        original = scope.begin_request("Run integer entry", source_id="human:1")
        waiting = json.loads(await run(context(facade, original), "Integer entry"))
        invalid = scope.begin_request("true", source_id="human:2")
        with pytest.raises(ValueError):
            await answer(
                context(facade, invalid), "true", "integer-run", waiting["question_id"]
            )
        assert effects == []
        corrected = scope.begin_request("30", source_id="human:3")
        result = json.loads(
            await answer(
                context(facade, corrected), "30", "integer-run", waiting["question_id"]
            )
        )
        assert result["status"] == "done"
        assert effects == [30]
    finally:
        await service.aclose()
        await facade.aclose()


def final_speech(facade, request, text, *, tool_call=False):
    from livekit.agents import llm

    handle = SpeechHandle.create()
    facade.bind_speech(handle, request)
    items = [llm.ChatMessage(role="assistant", content=[text])]
    if tool_call:
        items.append(
            llm.FunctionCall(name="capture_screen", arguments="{}", call_id="capture")
        )
    handle._item_added(items)
    handle._mark_done()
    return handle


@pytest.mark.parametrize(
    ("report", "tool_call", "should_close"),
    [
        ("Notepad opened.", False, True),
        ("I'll click next.", False, False),
        ("I haven't opened it yet.", False, False),
        ("Notepad opened.", True, False),
    ],
)
async def test_only_settled_supported_final_report_closes_request(
    report, tool_call, should_close
):
    async def judge(request, action, history):
        if action.name == "complete_request":
            allowed = action.arguments[
                "assistant_report"
            ] == "Notepad opened." and bool(history)
            return AdmissionDecision(
                "allow" if allowed else "deny",
                "Compare report with observed action results",
            )
        return AdmissionDecision("allow", "Requested launch")

    effects = []
    scope = RequestScope(judge)
    facade = RequestScopeTools(scope)
    agent = ExampleAssistant(effects)
    tool = facade.wrap_tools(agent.tools)[0]
    request = scope.begin_request("Open Notepad", source_id="human:1")
    ctx = context(facade, request)
    await tool(ctx, "Notepad")
    ctx.speech_handle._mark_done()
    handle = final_speech(facade, request, report, tool_call=tool_call)
    await facade.review_completed_speech(handle)
    if should_close:
        with pytest.raises(ToolError):
            scope.require(request.id)
    else:
        assert scope.require(request.id) == request
    await facade.aclose()


async def test_stale_completion_verdict_cannot_close_new_human_request():
    judging, release = asyncio.Event(), asyncio.Event()

    async def judge(*args):
        judging.set()
        await release.wait()
        return AdmissionDecision("allow", "Observed final completion")

    scope = RequestScope(judge)
    facade = RequestScopeTools(scope)
    old = scope.begin_request("Open Notepad", source_id="human:1")
    handle = final_speech(facade, old, "Notepad opened.")
    job = asyncio.create_task(facade.review_completed_speech(handle))
    await judging.wait()
    new = scope.begin_request("Open Calculator", source_id="human:2")
    release.set()
    await job
    assert scope.require(new.id) == new
    await facade.aclose()


async def test_pending_speech_or_skill_question_prevents_completion():
    from request_scope_livekit import _Binding

    async def judge(*args):
        return AdmissionDecision("allow", "Would otherwise close")

    scope = RequestScope(judge)
    facade = RequestScopeTools(scope)
    request = scope.begin_request(
        "Open Notepad then enter my answer", source_id="human:1"
    )
    pending = context(facade, request).speech_handle
    handle = final_speech(facade, request, "Notepad opened.")
    await facade.review_completed_speech(handle)
    assert scope.require(request.id) == request
    pending._mark_done()
    facade._remember_waiting(
        {
            "status": "waiting",
            "run_id": "run",
            "question_id": "q",
            "question": "What text?",
        },
        _Binding(scope, request),
    )
    handle = final_speech(facade, request, "Notepad opened.")
    await facade.review_completed_speech(handle)
    assert scope.require(request.id) == request
    await facade.aclose()


async def test_tool_awaiting_admission_blocks_completion_before_permit_exists():
    judging, release = asyncio.Event(), asyncio.Event()
    completion_checks = []

    async def judge(request, action, history):
        if action.name == "complete_request":
            completion_checks.append(action)
        else:
            judging.set()
            await release.wait()
        return AdmissionDecision("allow", "Supported action")

    scope = RequestScope(judge)
    facade = RequestScopeTools(scope)
    request = scope.begin_request("Open Notepad", source_id="human:1")
    ctx = context(facade, request)
    # Exercise the admission tracker even if SDK speech has already settled.
    ctx.speech_handle._mark_done()
    wrapped = facade.wrap_tools(ExampleAssistant([]).tools)[0]
    job = asyncio.create_task(wrapped(ctx, "Notepad"))
    await judging.wait()
    handle = final_speech(facade, request, "Notepad opened.")
    await facade.review_completed_speech(handle)
    assert not completion_checks
    assert scope.require(request.id) == request
    release.set()
    await job
    await facade.aclose()


async def test_new_tool_during_completion_check_invalidates_even_settled_verdict():
    judging, release = asyncio.Event(), asyncio.Event()

    async def judge(request, action, history):
        if action.name == "complete_request":
            judging.set()
            await release.wait()
        return AdmissionDecision("allow", "Supported action")

    scope = RequestScope(judge)
    facade = RequestScopeTools(scope)
    request = scope.begin_request("Open Notepad", source_id="human:1")
    handle = final_speech(facade, request, "Notepad opened.")
    job = asyncio.create_task(facade.review_completed_speech(handle))
    await judging.wait()
    ctx = context(facade, request)
    wrapped = facade.wrap_tools(ExampleAssistant([]).tools)[0]
    await wrapped(ctx, "Notepad")
    ctx.speech_handle._mark_done()
    release.set()
    await job
    assert scope.require(request.id) == request
    await facade.aclose()


@pytest.mark.parametrize(
    "failure", [RuntimeError("offline"), AdmissionDecision("allow", "")]
)
async def test_completion_policy_outage_or_invalid_verdict_leaves_request_open(failure):
    async def judge(*args):
        if isinstance(failure, Exception):
            raise failure
        return failure

    scope = RequestScope(judge)
    facade = RequestScopeTools(scope)
    request = scope.begin_request("Open Notepad", source_id="human:1")
    await facade.review_completed_speech(
        final_speech(facade, request, "Notepad opened.")
    )
    assert scope.require(request.id) == request
    await facade.aclose()


async def test_done_callback_can_close_explicit_unresolved_stop_without_reporting_success():
    judged = asyncio.Event()
    reports = []

    async def judge(request, action, history):
        reports.append(action.arguments["assistant_report"])
        judged.set()
        return AdmissionDecision("allow", "Assistant has explicitly stopped unresolved")

    scope = RequestScope(judge)
    facade = RequestScopeTools(scope)
    request = scope.begin_request("Open Notepad", source_id="human:1")
    report = "I could not open Notepad. I have stopped and made no further changes."
    final_speech(facade, request, report)
    await asyncio.wait_for(judged.wait(), 1)
    await asyncio.gather(*facade._background)
    with pytest.raises(ToolError, match="Request closed"):
        scope.require(request.id)
    assert reports == [report]
    assert scope.history == ()
    await facade.aclose()


async def test_pending_direct_clarification_never_invokes_completion_judge():
    checks = []

    async def judge(*args):
        checks.append(args)
        return AdmissionDecision("allow", "Would otherwise close")

    scope = RequestScope(judge)
    facade = RequestScopeTools(scope)
    request = scope.begin_request("Choose a profile", source_id="human:1")
    facade._clarification = (request, "Which profile?")
    await facade.review_completed_speech(
        final_speech(facade, request, "Which profile?")
    )
    assert not checks
    assert facade._clarification == (request, "Which profile?")
    assert scope.require(request.id) == request
    await facade.aclose()


async def test_initial_voice_handle_receives_completion_callback_before_transcript():
    from livekit.agents import llm

    reviewed = asyncio.Event()
    requests = []

    async def judge(request, action, history):
        requests.append(request.text)
        reviewed.set()
        return AdmissionDecision("allow", "Supported observation report")

    scope = RequestScope(judge)
    facade = RequestScopeTools(scope)
    session = Session()
    facade.attach_session(session)
    session._activity._rt_session._current_generation = SimpleNamespace(
        input_id="voice-1"
    )
    handle = SpeechHandle.create()
    session.emit(
        "speech_created",
        SpeechCreatedEvent(
            speech_handle=handle, user_initiated=False, source="generate_reply"
        ),
    )
    assert facade._speeches[handle.id] is None
    session.emit(
        "user_input_transcribed",
        UserInputTranscribedEvent(
            transcript="Describe the screen", is_final=True, item_id="voice-1"
        ),
    )
    request = scope.current_request
    handle._item_added(
        [llm.ChatMessage(role="assistant", content=["The profile picker is open."])]
    )
    handle._mark_done()
    await asyncio.wait_for(reviewed.wait(), 1)
    await asyncio.gather(*facade._background)
    assert requests == ["Describe the screen"]
    with pytest.raises(ToolError, match="Request closed"):
        scope.require(request.id)
    await facade.aclose()


def voice_context(session, source):
    session._activity._rt_session._current_generation = SimpleNamespace(input_id=source)
    handle = SpeechHandle.create()
    session.emit(
        "speech_created",
        SpeechCreatedEvent(
            speech_handle=handle, user_initiated=False, source="generate_reply"
        ),
    )
    return SimpleNamespace(speech_handle=handle)


def send_transcript(session, source, text="Open Notepad", *, final=True):
    session.emit(
        "user_input_transcribed",
        UserInputTranscribedEvent(transcript=text, is_final=final, item_id=source),
    )


async def test_separate_generation_cannot_adopt_active_voice_request():
    effects = []
    agent, _, facade, _ = setup_facade(effects)
    facade.final_transcript_timeout = 0.01
    session = Session()
    facade.attach_session(session)
    voice_context(session, "voice-1")
    send_transcript(session, "voice-1")
    with pytest.raises(ToolError):
        await agent.tools[0](voice_context(session, "voice-2"), "Notepad")
    assert effects == []
    await facade.aclose()


@pytest.mark.parametrize("interim", [False, True])
async def test_tool_cannot_supply_missing_final_transcription(interim):
    effects = []
    agent, scope, facade, _ = setup_facade(effects)
    facade.final_transcript_timeout = 0.01
    session = Session()
    facade.attach_session(session)
    ctx = voice_context(session, "voice-1")
    if interim:
        send_transcript(session, "voice-1", final=False)
    with pytest.raises(ToolError):
        await agent.tools[0](ctx, "Notepad")
    assert effects == []
    assert scope.current_request is None
    await facade.aclose()


async def test_interim_stop_transcript_stops_scope_and_rejects_subsequent_tool():
    effects = []
    agent, _, facade, _ = setup_facade(effects)
    session = Session()
    facade.attach_session(session)
    ctx = voice_context(session, "voice-1")
    send_transcript(session, "voice-1", "Stop IRA!", final=False)
    with pytest.raises(ToolError):
        await agent.tools[0](ctx, "Notepad")
    assert effects == []
    await facade.aclose()


async def test_multiple_tools_for_one_finalized_voice_generation_remain_bound():
    effects = []
    agent, _, facade, _ = setup_facade(effects)
    session = Session()
    facade.attach_session(session)
    first = voice_context(session, "voice-1")
    send_transcript(session, "voice-1")
    await agent.tools[0](first, "Notepad")
    second = voice_context(session, "voice-1")
    await agent.tools[0](second, "Notepad")
    assert effects == ["Notepad", "Notepad"]
    await facade.aclose()


async def test_new_partial_instruction_does_not_resume_old_voice_request():
    effects = []
    agent, _, facade, _ = setup_facade(effects)
    session = Session()
    facade.attach_session(session)
    first = voice_context(session, "voice-1")
    send_transcript(session, "voice-1")
    await agent.tools[0](first, "Notepad")
    send_transcript(session, "voice-2", "Actually do not open that", final=False)
    with pytest.raises(ToolError):
        await agent.tools[0](first, "Notepad")
    assert effects == ["Notepad"]
    await facade.aclose()
