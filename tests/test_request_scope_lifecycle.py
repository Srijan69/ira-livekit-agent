"""Admission rechecks procedure identity and retires only replaced task state."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from livekit.agents import ToolError
from livekit.agents.voice.events import UserInputTranscribedEvent
from livekit.agents.voice.speech_handle import SpeechHandle
from test_request_scope_livekit import ExampleAssistant, Session, context

from request_scope import AdmissionDecision, RequestScope, ScopeViolation
from request_scope_livekit import RequestScopeTools, _Binding


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_changed_procedure_is_rejected_before_policy_and_host_effect(
    asynchronous,
):
    effects, judged = [], []

    def validate(request_id, name, arguments):
        assert request_id == request.id
        assert name == "open_app"
        assert arguments == {"app_name": "Notepad"}
        raise ScopeViolation("The selected procedure changed")

    async def async_validate(*args):
        validate(*args)

    async def judge(*_):
        judged.append("policy")
        return AdmissionDecision("allow", "Requested launch")

    scope = RequestScope(judge)
    facade = RequestScopeTools(
        scope, before_action=async_validate if asynchronous else validate
    )
    request = scope.begin_request("Open Notepad", source_id="current")
    tool = facade.wrap_tools(ExampleAssistant(effects).tools)[0]
    with pytest.raises(ToolError, match="procedure changed"):
        await tool(context(facade, request), "Notepad")
    assert effects == []
    assert judged == []
    assert scope.history == ()
    await facade.aclose()


async def test_procedure_changed_during_policy_is_rechecked_before_invoke():
    effects = []
    current = [True]
    validations = []

    async def validate(request_id, name, arguments):
        validations.append((name, arguments))
        if not current[0]:
            raise ScopeViolation("The selected procedure changed")

    async def judge(*_):
        current[0] = False
        return AdmissionDecision("allow", "Requested launch")

    scope = RequestScope(judge)
    facade = RequestScopeTools(scope, before_action=validate)
    request = scope.begin_request("Open Notepad", source_id="current")
    tool = facade.wrap_tools(ExampleAssistant(effects).tools)[0]
    with pytest.raises(ToolError, match="procedure changed"):
        await tool(context(facade, request), "Notepad")
    assert effects == []
    assert validations == [
        ("open_app", {"app_name": "Notepad"}),
        ("open_app", {"app_name": "Notepad"}),
    ]
    assert scope.history[-1]["outcome"] == "not_started"

    # A refused validation cannot falsely mark a never-started effect uncertain.
    async def allow(*_):
        return AdmissionDecision("allow", "Requested launch")

    scope.judge = allow
    current[0] = True
    await tool(context(facade, request), "Notepad")
    assert effects == ["Notepad"]
    await facade.aclose()


class TextSession(Session):
    @asynccontextmanager
    async def _claim_user_turn(self):
        yield

    async def interrupt(self):
        pass

    def generate_reply(self, user_input):
        return SpeechHandle.create()


@pytest.mark.parametrize("channel", ["voice", "text"])
async def test_finalized_replacement_drains_adaptive_cleanup_without_stopping_waiting_workflow(
    channel,
):
    changed, stopped = [], []

    async def cleanup(old_request_id, new_request_id):
        await asyncio.sleep(0)
        changed.append((old_request_id, new_request_id))

    scope = RequestScope()
    facade = RequestScopeTools(
        scope, on_request_changed=cleanup, on_stop=lambda: stopped.append("stop")
    )
    session = TextSession()
    facade.attach_session(session)
    old = scope.begin_request("Run note procedure", source_id="old")
    facade._remember_waiting(
        {
            "status": "waiting",
            "run_id": "compiled-run",
            "question_id": "q",
            "question": "What text?",
        },
        _Binding(scope, old),
    )
    if channel == "voice":
        event = UserInputTranscribedEvent(
            transcript="hello", is_final=False, item_id="new"
        )
        session.emit("user_input_transcribed", event)
        assert changed == []
        event = UserInputTranscribedEvent(
            transcript="hello", is_final=True, item_id="new"
        )
        session.emit("user_input_transcribed", event)
        new = scope.current_request
        session.emit("user_input_transcribed", event)
    else:
        event = SimpleNamespace(text="hello", info=SimpleNamespace(stream_id="new"))
        await facade.text_input_cb(session, event)
        new = scope.current_request
        await facade.text_input_cb(session, event)
    assert new.id != old.id
    assert "compiled-run" in facade._waiting
    assert stopped == []
    await facade.aclose()
    assert changed == [(old.id, new.id)]
    assert stopped == []
    # Late session events and duplicate shutdown cannot repeat cleanup.
    await facade.aclose()
    if channel == "voice":
        session.emit("user_input_transcribed", event)
    assert changed == [(old.id, new.id)]
