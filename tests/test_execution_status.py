"""Execution metadata stays correlated without changing tool or voice behavior."""

import asyncio
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from livekit.agents import (
    ToolCallEnded,
    ToolCallStarted,
    ToolCallUpdated,
    ToolError,
    ToolExecutionUpdatedEvent,
)
from livekit.agents.llm import FunctionCall

from agent import Assistant, _attach_status_reporter
from browser import BrowserUnavailableError
from skill_format import Action, Check, Skill, Step
from skill_store import SkillStore
from telemetry import SessionStatusReporter


def tool_context(call_id: str, name: str) -> SimpleNamespace:
    return SimpleNamespace(
        function_call=FunctionCall(call_id=call_id, name=name, arguments="{}")
    )


@pytest.fixture
def status_events():
    events: list[dict] = []
    reporter = SessionStatusReporter(
        run_id="session-1", sink=lambda _: None, observer=events.append
    )
    return reporter, events


@pytest.fixture
def tool_updates(status_events):
    reporter, events = status_events
    callbacks = {}

    class Session:
        def on(self, name, callback):
            callbacks[name] = callback

    _attach_status_reporter(Session(), reporter)
    return callbacks["tool_execution_updated"], events


def test_sdk_lifecycle_keeps_ids_names_and_safe_terminal_detail(tool_updates) -> None:
    update, events = tool_updates
    for call_id, name in [("call-a", "open_app"), ("call-b", "read_page")]:
        update(
            ToolExecutionUpdatedEvent(
                update=ToolCallStarted(
                    function_call=FunctionCall(
                        call_id=call_id,
                        name=name,
                        arguments='{"secret":"never publish"}',
                    )
                )
            )
        )
    update(
        ToolExecutionUpdatedEvent(
            update=ToolCallUpdated(
                id="call-a_update_1", call_id="call-a", message="Checking desktop"
            )
        )
    )
    update(
        ToolExecutionUpdatedEvent(
            update=ToolCallEnded(
                id="call-b_final",
                call_id="call-b",
                status="done",
                message="raw page output never publish",
            )
        )
    )
    update(
        ToolExecutionUpdatedEvent(
            update=ToolCallEnded(
                id="call-a_final",
                call_id="call-a",
                status="error",
                message="raw SDK error never publish",
            )
        )
    )

    assert [(e.get("call_id"), e.get("tool_name"), e["state"]) for e in events] == [
        ("call-a", "open_app", "DELEGATING"),
        ("call-b", "read_page", "DELEGATING"),
        ("call-a", "open_app", "RUNNING"),
        ("call-b", "read_page", "DONE"),
        ("call-a", "open_app", "ERROR"),
    ]
    assert all(e.get("source") == "lifecycle" for e in events)
    assert events[2]["detail"] == "Checking desktop"
    assert events[3]["detail"] == "read_page"
    assert events[4]["detail"] == "open_app"
    assert all("never publish" not in str(e) for e in events)


def test_sdk_cancellation_is_distinct_from_failure(tool_updates) -> None:
    update, events = tool_updates
    update(
        ToolExecutionUpdatedEvent(
            update=ToolCallEnded(
                id="orphan-final",
                call_id="orphan-call",
                status="cancelled",
                message="raw cancellation detail",
            )
        )
    )

    assert events[0]["state"] == "CANCELLED"
    assert events[0].get("call_id") == "orphan-call"
    assert events[0].get("source") == "lifecycle"
    assert events[0]["detail"] == "tool call"


async def test_open_app_manual_events_bind_to_its_call(status_events) -> None:
    reporter, events = status_events
    assistant = Assistant(reporter=reporter)

    with patch("agent._launch_via_start_menu"):
        result = await assistant.open_app(tool_context("app-1", "open_app"), "Spotify")

    assert "Asked Windows" in result
    assert [e["state"] for e in events] == ["DELEGATING", "RUNNING", "DONE"]
    assert all(
        (e.get("call_id"), e.get("tool_name"), e.get("source"))
        == ("app-1", "open_app", "tool")
        for e in events
    )


async def test_screen_capture_manual_events_bind_to_its_call(
    status_events, desktop_view
) -> None:
    reporter, events = status_events

    desktop, _, context, _ = desktop_view
    context.function_call = tool_context("screen-1", "capture_screen").function_call
    result = await Assistant(reporter=reporter, desktop=desktop).capture_screen(context)
    assert '"coordinate_space": "image_pixels"' in result
    assert [e["state"] for e in events] == ["DELEGATING", "DONE"]
    assert all(
        (e.get("call_id"), e.get("tool_name"), e.get("source"))
        == ("screen-1", "capture_screen", "tool")
        for e in events
    )


async def test_browser_internal_error_keeps_call_identity(status_events) -> None:
    reporter, events = status_events

    class Browser:
        def navigate(self, _url):
            raise BrowserUnavailableError("not available")

    with pytest.raises(ToolError, match="not available"):
        await Assistant(reporter=reporter, browser=Browser()).browse_to(
            tool_context("browser-1", "browse_to"), "example.com"
        )

    assert events[-1]["state"] == "ERROR"
    assert all(
        (e.get("call_id"), e.get("tool_name"), e.get("source"))
        == ("browser-1", "browse_to", "tool")
        for e in events
    )


async def test_overlapping_browser_calls_do_not_share_a_reporter(status_events) -> None:
    reporter, events = status_events
    first_started = Event()
    finish_first = Event()
    second_started = Event()

    class Browser:
        def read_page(self):
            first_started.set()
            finish_first.wait(2)
            return "Read page"

        def back(self):
            second_started.set()
            return "Went back"

    assistant = Assistant(reporter=reporter, browser=Browser())

    first = asyncio.create_task(
        assistant.read_page(tool_context("read-1", "read_page"))
    )
    second = None
    try:
        assert await asyncio.to_thread(first_started.wait, 1)
        second = asyncio.create_task(
            assistant.go_back(tool_context("back-1", "go_back"))
        )
        await asyncio.sleep(0)
        assert not second_started.is_set(), (
            "shared browser ownership must serialize calls"
        )
        finish_first.set()
        assert await first == "Read page"
        assert await second == "Went back"
    finally:
        finish_first.set()
        await asyncio.gather(
            first, *([second] if second is not None else []), return_exceptions=True
        )

    assert [(e.get("call_id"), e.get("tool_name")) for e in events] == [
        ("read-1", "read_page"),
        ("read-1", "read_page"),
        ("back-1", "go_back"),
        ("back-1", "go_back"),
        ("read-1", "read_page"),
        ("back-1", "go_back"),
    ]


async def test_skill_progress_and_observations_share_enclosing_call(
    status_events, tmp_path
) -> None:
    reporter, events = status_events
    store = SkillStore(tmp_path)
    store.save(
        Skill(
            "Browse example",
            "Open the example page",
            (
                Step(
                    1,
                    "Navigate",
                    action=Action(
                        "tool", tool="browse_to", args=(("url", "https://example.com"),)
                    ),
                    check=Check("page_contains", "Example"),
                ),
            ),
            allowed_tools=("browse_to",),
            state="enabled",
        ),
        source="compiled",
    )

    class Browser:
        visited = None

        def navigate(self, _url):
            self.visited = _url
            return "Example page"

        def observe(self, kind, value):
            return self.visited == "https://example.com" and (kind, value) == (
                "page_contains",
                "Example",
            )

    result = await Assistant(
        reporter=reporter, browser=Browser(), skills=store
    ).run_skill(tool_context("skill-1", "run_skill"), "Browse example")

    assert "completed" in result
    assert any(e["state"] == "VERIFYING" for e in events)
    assert events[-1]["outcome"] == "done"
    assert all(e["skill_run_id"] == "session-1:skill-1" for e in events)
    assert all(e["run_id"] == "session-1" for e in events)
    assert all(
        (e.get("call_id"), e.get("tool_name"), e.get("source"))
        == ("skill-1", "run_skill", "skill")
        for e in events
    )
