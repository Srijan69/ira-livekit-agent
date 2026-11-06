"""Admission must gate the actual Assistant surface and physical boundary."""

import asyncio
import json
import sys
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from livekit.agents import ToolError
from livekit.agents.voice.speech_handle import SpeechHandle

from agent import Assistant
from request_scope import AdmissionDecision, RequestScope
from skill_store import SkillStore


def exported(assistant, name):
    return next(tool for tool in assistant.tools if tool.info.name == name)


def bind(assistant, context, text):
    context.speech_handle = SpeechHandle.create()
    request = assistant._request_scope.begin_request(text, source_id="human-1")
    assistant._scoped_tools.bind_speech(context.speech_handle, request)


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")


async def test_exported_capture_cannot_authorize_a_click(desktop_view, tmp_path):
    async def judge(request, proposal, history):
        return AdmissionDecision(
            "allow" if proposal.kind == "observe" else "clarify",
            "Only inspect the screen",
        )

    desktop, backend, context, _ = desktop_view
    effects = []
    backend.click = lambda *a, **kw: effects.append(a)
    assistant = Assistant(
        desktop=desktop, skills=SkillStore(tmp_path), request_scope=RequestScope(judge)
    )
    bind(assistant, context, "Capture my screen and tell me what is visible")
    frame = json.loads(await exported(assistant, "capture_screen")(context))
    with pytest.raises(ToolError):
        await exported(assistant, "click_mouse")(
            context, 100, 100, frame["observation_id"]
        )
    assert effects == []


async def test_stopped_action_is_rechecked_after_marker_start(desktop_view, tmp_path):
    async def judge(*_):
        return AdmissionDecision("allow", "Requested click")

    desktop, backend, context, _ = desktop_view
    effects = []
    backend.click = lambda *a, **kw: effects.append(a)
    scope = RequestScope(judge)
    assistant = Assistant(
        desktop=desktop, skills=SkillStore(tmp_path), request_scope=scope
    )
    bind(assistant, context, "Click the button")
    frame = json.loads(await exported(assistant, "capture_screen")(context))

    @contextmanager
    def marker(*_):
        scope.stop()
        yield

    assistant._computer.indicator = marker
    with pytest.raises(ToolError):
        await exported(assistant, "click_mouse")(
            context, 100, 100, frame["observation_id"]
        )
    assert effects == []


async def test_queued_app_launch_cannot_use_new_requests_authority(
    tmp_path, monkeypatch
):
    admitted = asyncio.Event()

    async def judge(*_):
        admitted.set()
        return AdmissionDecision("allow", "Requested launch")

    effects = []
    monkeypatch.setattr("agent._launch_via_start_menu", effects.append)
    scope = RequestScope(judge)
    assistant = Assistant(skills=SkillStore(tmp_path), request_scope=scope)
    context = SimpleNamespace()
    bind(assistant, context, "Open Chrome")
    await assistant._execution_lock.acquire()
    task = asyncio.create_task(exported(assistant, "open_app")(context, "Chrome"))
    await admitted.wait()
    scope.begin_request("Only inspect the screen", source_id="human-2")
    assistant._execution_lock.release()
    with pytest.raises(ToolError):
        await task
    assert effects == []


async def test_exported_skill_admission_does_not_authorize_unrelated_step(tmp_path):
    from test_agent_skill_tools import page_skill

    async def judge(request, proposal, history):
        return AdmissionDecision(
            "allow" if proposal.kind == "orchestrate" else "deny",
            "The step is outside the task",
        )

    effects = []
    store = SkillStore(tmp_path)
    store.save(page_skill(), source="compiled")
    browser = SimpleNamespace(navigate=effects.append, observe=lambda *_: True)
    assistant = Assistant(
        browser=browser, skills=store, request_scope=RequestScope(judge)
    )
    context = SimpleNamespace()
    bind(assistant, context, "Run Read example")
    with pytest.raises(ToolError):
        await exported(assistant, "run_skill")(context, "read-example")
    assert effects == []


@pytest.mark.parametrize("wait_number", [1, 2])
@pytest.mark.parametrize("interruption", ["stop", "new_request"])
async def test_app_launch_stops_between_native_steps(
    tmp_path, monkeypatch, wait_number, interruption
):
    async def judge(*_):
        return AdmissionDecision("allow", "Requested launch")

    effects = []
    monkeypatch.setitem(
        sys.modules,
        "pyautogui",
        SimpleNamespace(
            press=lambda key: effects.append(("press", key)),
            write=lambda text, **_: effects.append(("write", text)),
        ),
    )
    scope = RequestScope(judge)
    waits = 0

    def interrupt_during_wait(_):
        nonlocal waits
        waits += 1
        if waits == wait_number:
            if interruption == "stop":
                scope.stop()
            else:
                scope.begin_request("Inspect only", source_id="human-2")

    monkeypatch.setattr("agent.time.sleep", interrupt_during_wait)
    assistant = Assistant(skills=SkillStore(tmp_path), request_scope=scope)
    context = SimpleNamespace()
    bind(assistant, context, "Open Chrome")
    with pytest.raises(ToolError):
        await exported(assistant, "open_app")(context, "Chrome")
    expected = [("press", "win")]
    if wait_number == 2:
        expected.append(("write", "Chrome"))
    assert effects == expected
