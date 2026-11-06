"""Timeouts cross real SDK dispatch and shutdown without replaying native work."""

import asyncio
import json
import sys
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from livekit.agents import AgentSession, RunContext, ToolError, function_tool
from livekit.agents.llm import FunctionCall
from livekit.agents.voice.speech_handle import SpeechHandle
from livekit.agents.voice.tool_executor import _ToolExecutor
from PIL import Image

from agent import Assistant, _attach_status_reporter
from desktop import DesktopController
from host_execution import HostActionTimeout, HostExecutionLock
from request_scope import AdmissionDecision, RequestScope
from telemetry import SessionStatusReporter


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("open_app", {"app_name": "Notepad"}),
        ("capture_screen", {}),
        (
            "inspect_screen",
            {"observation_id": "frame", "x": 0, "y": 0, "width": 1, "height": 1},
        ),
        ("focus_desktop_window", {"window_id": "window", "observation_id": "frame"}),
        ("move_mouse", {"x": 0, "y": 0, "observation_id": "frame"}),
        ("browse_to", {"url": "https://example.com"}),
        ("generate_market_report", {}),
        (
            "create_styled_spreadsheet",
            {"data_json": '[{"name":"Asset","price":1,"change_24h":1}]'},
        ),
        ("scrape_market_assets", {}),
        ("optimize_image_assets", {}),
        ("resize_image_file", {"image_path": "inert.jpg"}),
    ],
)
async def test_sdk_timeout_is_terminal_error_with_no_late_done(
    tmp_path, monkeypatch, name, arguments
):
    started, finish = Event(), Event()
    effects = []
    events = []

    def blocked(*args, **kwargs):
        started.set()
        finish.wait(2)
        effects.append(name)
        if name in {"generate_market_report", "scrape_market_assets"}:
            return [{"name": "Asset", "price": 1, "change_24h": 1}]
        if name == "optimize_image_assets":
            return [Path("inert.jpg")]
        return "late result"

    reporter = SessionStatusReporter(
        run_id="timeouts", sink=lambda _: None, observer=events.append
    )
    assistant = Assistant(reporter=reporter, browser=SimpleNamespace(navigate=blocked))
    assistant._execution_lock = HostExecutionLock(tmp_path / "host.lock")
    assistant._host_action_timeout_seconds = 0.04
    assistant._computer = SimpleNamespace(
        invalidate=lambda: None,
        observe=blocked,
        detail=blocked,
        focus=blocked,
        act=blocked,
    )
    # The SDK session is offline; substitute only session-video admission and
    # physical desktop/browser boundaries, retaining dispatch and result handling.
    monkeypatch.setattr(assistant, "_require_desktop_activity", lambda *a, **kw: None)
    monkeypatch.setattr(
        "agent._launch_via_start_menu",
        blocked if name == "open_app" else lambda _: None,
    )
    monkeypatch.setattr("agent.market_report.scrape_market_assets", blocked)
    monkeypatch.setattr(
        "agent.market_report.create_market_report",
        blocked
        if name == "create_styled_spreadsheet"
        else lambda *a, **kw: "inert.xlsx",
    )
    monkeypatch.setattr(
        "agent.market_report.launch_summary_editor", lambda _: "inert.txt"
    )
    monkeypatch.setattr("agent.image_pipeline.run_image_pipeline", blocked)
    monkeypatch.setattr("agent.image_pipeline.resize_image", blocked)
    session = AgentSession()
    _attach_status_reporter(session, reporter)
    context = RunContext(
        session=session,
        speech_handle=SpeechHandle.create(),
        function_call=FunctionCall(
            name=name, call_id="timeout-call", arguments=json.dumps(arguments)
        ),
    )
    executor = _ToolExecutor()
    try:
        with pytest.raises(HostActionTimeout) as failure:
            await asyncio.wait_for(
                executor.execute(
                    tool=function_tool(getattr(assistant, name)),
                    run_ctx=context,
                    raw_arguments=arguments,
                ),
                0.5,
            )
        assert started.is_set()
        payload = json.loads(str(failure.value))
        assert payload["status"] == "host_action_timeout"
        assert payload["retryable"] is False
        assert payload["effect_started"] is True
        await asyncio.sleep(0)
        terminal = [
            e
            for e in events
            if e.get("source") == "lifecycle" and e["state"] in {"ERROR", "DONE"}
        ]
        assert [(e["call_id"], e["tool_name"], e["state"]) for e in terminal] == [
            ("timeout-call", name, "ERROR")
        ]
        assert not any(e["state"] == "DONE" for e in events)
        assert assistant._execution_lock.locked()
    finally:
        finish.set()
        await asyncio.gather(*tuple(assistant._host_jobs), return_exceptions=True)
        await executor.aclose()
    assert effects == [name]
    assert not any(e["state"] == "DONE" for e in events)
    assert not assistant._execution_lock.locked()


@pytest.mark.parametrize(
    ("name", "human_request", "provider_arguments", "canonical_arguments"),
    [
        (
            "open_app",
            "Open Notepad",
            {"name": "Notepad"},
            {"app_name": "Notepad"},
        ),
        (
            "browse_to",
            "Open example.com",
            {"url": "https://example.com"},
            {"url": "https://example.com"},
        ),
    ],
)
async def test_exported_sdk_timeout_records_uncertainty_and_blocks_replay(
    tmp_path,
    monkeypatch,
    name,
    human_request,
    provider_arguments,
    canonical_arguments,
):
    """Exercise the shipped scope/argument wrappers, not a re-decorated method."""
    started, finish = Event(), Event()
    invocations, completed, admitted, events = [], [], [], []

    async def judge(request, proposal, history):
        admitted.append((request.text, proposal.name, dict(proposal.arguments)))
        return AdmissionDecision("allow", "Explicit matching human request")

    def blocked(argument):
        invocations.append(argument)
        started.set()
        finish.wait(2)
        completed.append(argument)
        return "late result"

    scope = RequestScope(judge)
    reporter = SessionStatusReporter(
        run_id="exported-timeouts", sink=lambda _: None, observer=events.append
    )
    assistant = Assistant(
        request_scope=scope,
        reporter=reporter,
        browser=SimpleNamespace(navigate=blocked),
    )
    assistant._execution_lock = HostExecutionLock(tmp_path / "host.lock")
    assistant._host_action_timeout_seconds = 0.04
    monkeypatch.setattr("agent._launch_via_start_menu", blocked)
    tool = next(tool for tool in assistant.tools if tool.info.name == name)
    session = AgentSession()
    _attach_status_reporter(session, reporter)
    request = scope.begin_request(human_request, source_id="human:exported")
    executor = _ToolExecutor()

    async def execute(call_id, arguments):
        speech = SpeechHandle.create()
        assistant._scoped_tools.bind_speech(speech, request)
        context = RunContext(
            session=session,
            speech_handle=speech,
            function_call=FunctionCall(
                name=name, call_id=call_id, arguments=json.dumps(arguments)
            ),
        )
        return await asyncio.wait_for(
            executor.execute(tool=tool, run_ctx=context, raw_arguments=arguments),
            0.5,
        )

    try:
        with pytest.raises(HostActionTimeout) as failure:
            await execute("exported-timeout", provider_arguments)
        assert started.is_set()
        payload = json.loads(str(failure.value))
        assert payload["status"] == "host_action_timeout"
        assert payload["effect_started"] is True
        assert payload["retryable"] is False
        assert payload["outcome"] == "uncertain"
        assert admitted == [(human_request, name, canonical_arguments)]
        assert [
            (entry["tool"], entry["arguments"], entry["outcome"])
            for entry in scope.history
        ] == [(name, canonical_arguments, "uncertain")]
        assert assistant._execution_lock.locked()
        with pytest.raises(ToolError, match="Previous effect is uncertain"):
            await execute("replay-running", canonical_arguments)
        assert len(invocations) == 1
        assert completed == []

        finish.set()
        await asyncio.gather(*tuple(assistant._host_jobs), return_exceptions=True)
        assert completed == invocations
        assert not assistant._execution_lock.locked()
        with pytest.raises(ToolError, match="Previous effect is uncertain"):
            await execute("replay-settled", canonical_arguments)
        assert len(invocations) == 1
        assert len(admitted) == 1
        assert [entry["outcome"] for entry in scope.history] == ["uncertain"]
        await asyncio.sleep(0)
        terminals = [
            (event["call_id"], event["tool_name"], event["state"])
            for event in events
            if event.get("source") == "lifecycle"
            and event["state"] in {"ERROR", "DONE"}
        ]
        assert terminals == [
            ("exported-timeout", name, "ERROR"),
            ("replay-running", name, "ERROR"),
            ("replay-settled", name, "ERROR"),
        ]
        assert not any(event["state"] == "DONE" for event in events)
    finally:
        finish.set()
        await asyncio.gather(*tuple(assistant._host_jobs), return_exceptions=True)
        await executor.aclose()
        await assistant._scoped_tools.aclose()


@pytest.mark.parametrize("revoke_before_launch", [False, True])
async def test_optimizer_real_desktop_uses_guarded_launcher(
    tmp_path, monkeypatch, revoke_before_launch
):
    """The real pipeline reaches the guarded launcher with its original permit."""

    async def judge(request, proposal, history):
        return AdmissionDecision("allow", "Explicit image optimization request")

    scope = RequestScope(judge)
    assistant = Assistant(desktop=DesktopController(), request_scope=scope)
    assistant._execution_lock = HostExecutionLock(tmp_path / "host.lock")
    source = tmp_path / "source.jpg"
    Image.new("RGB", (8, 8), "blue").save(source)
    inputs = []

    def record(kind, value):
        assert assistant._execution_lock.locked()
        inputs.append((kind, value))

    monkeypatch.setitem(
        sys.modules,
        "pyautogui",
        SimpleNamespace(
            press=lambda key: record("press", key),
            write=lambda text, **kwargs: record("write", text),
        ),
    )
    monkeypatch.setattr("agent._START_MENU_OPEN_DELAY", 0)
    monkeypatch.setattr("agent._SEARCH_RESULTS_DELAY", 0)
    monkeypatch.setattr(
        "agent.image_pipeline.scrape_image_urls",
        lambda **kwargs: ["https://example.com/inert.jpg"],
    )

    def downloaded(*args, **kwargs):
        if revoke_before_launch:
            scope.revoke("Stopped before editor launch")
        return [source]

    monkeypatch.setattr("agent.image_pipeline.download_images", downloaded)
    monkeypatch.setattr(
        "agent.image_pipeline.subprocess.Popen",
        lambda *a, **kw: pytest.fail("Unexpected unguarded editor launch"),
    )
    request = scope.begin_request(
        "Optimize an image and open Paint", source_id="human:image"
    )
    speech = SpeechHandle.create()
    assistant._scoped_tools.bind_speech(speech, request)
    context = SimpleNamespace(
        speech_handle=speech,
        function_call=FunctionCall(
            name="optimize_image_assets", call_id="image-call", arguments="{}"
        ),
    )
    tool = next(
        tool for tool in assistant.tools if tool.info.name == "optimize_image_assets"
    )
    if revoke_before_launch:
        with pytest.raises(ToolError, match="Stopped before editor launch"):
            await tool(context, count=1, output_dir=str(tmp_path / "output"))
        assert inputs == []
    else:
        result = await tool(context, count=1, output_dir=str(tmp_path / "output"))
        assert "Downloaded and resized 1 images" in result
        assert inputs == [("press", "win"), ("write", "MS Paint"), ("press", "enter")]
        with Image.open(tmp_path / "output" / "city_01.jpg") as output:
            assert output.size == (1080, 1080)
    assert not assistant._execution_lock.locked()


async def test_on_exit_returns_bounded_and_defers_cleanup_until_worker_settles(
    tmp_path,
):
    started, finish, closed = Event(), Event(), Event()
    assistant = Assistant()
    assistant._execution_lock = HostExecutionLock(tmp_path / "host.lock")
    assistant._host_action_timeout_seconds = 0.04
    assistant._host_shutdown_timeout_seconds = 0.04
    assistant._computer = SimpleNamespace(invalidate=lambda: None, close=closed.set)

    def blocked():
        started.set()
        finish.wait(2)

    call = asyncio.create_task(assistant._run_host_action(blocked))
    closing = None
    try:
        assert await asyncio.to_thread(started.wait, 1)
        closing = asyncio.create_task(assistant.on_exit())
        done, _ = await asyncio.wait({closing}, timeout=0.2)
        assert done, "session exit waited indefinitely for native work"
        await closing
        assert not closed.is_set(), "cleanup raced with an active native worker"
        assert assistant._execution_lock.locked()
        with pytest.raises(ToolError):
            await assistant._run_host_action(lambda: "new effect")
    finally:
        finish.set()
        await asyncio.gather(call, *tuple(assistant._host_jobs), return_exceptions=True)
        if closing is not None:
            await closing
        if cleanup := getattr(assistant, "_host_cleanup_task", None):
            await asyncio.wait_for(asyncio.shield(cleanup), 1)
    assert closed.is_set()
    assert not assistant._execution_lock.locked()
