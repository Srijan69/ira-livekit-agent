"""Actual tool boundaries expose fresh recovery evidence and visual state."""

import json
from types import SimpleNamespace

import pytest
from livekit.agents import ToolError

from agent import Assistant
from host_execution import HostActionTimeout
from request_scope import ScopeViolation


async def test_capture_and_input_publish_correlated_before_after_frames(desktop_view):
    desktop, backend, context, _ = desktop_view
    effects = []
    backend.moveTo = lambda *args, **kwargs: None
    backend.click = lambda *args, **kwargs: effects.append("clicked")
    context.function_call = SimpleNamespace(call_id="capture-1", name="capture_screen")
    events = []
    agent = Assistant(desktop=desktop, publish_event=events.append)
    result = json.loads(await agent.capture_screen(context))
    assert events[0]["phase"] == "observed"
    assert events[0]["observation_id"] == result["observation_id"]
    assert events[0]["call_id"] == "capture-1"
    context.function_call = SimpleNamespace(call_id="click-1", name="click_mouse")
    output = json.loads(
        await agent.click_mouse(context, 40, 40, result["observation_id"])
    )
    assert effects == ["clicked"]
    assert [event["phase"] for event in events] == ["observed", "before", "after"]
    assert events[1]["action"]["image_points"] == [[40, 40]]
    assert events[2]["observation_id"] == output["observation_id"]
    assert events[1]["observation_id"] != events[2]["observation_id"]
    assert "action" not in events[2]
    assert [event["sequence"] for event in events] == [1, 2, 3]


async def test_failed_model_image_delivery_never_claims_observed_preview(desktop_view):
    desktop, _, _, _ = desktop_view
    events = []
    agent = Assistant(desktop=desktop, publish_event=events.append)
    with pytest.raises(ToolError):
        await agent.capture_screen(None)
    assert not any(event.get("phase") == "observed" for event in events)


async def test_frame_invalidated_during_preview_encoding_is_not_actionable(
    desktop_view,
):
    desktop, _, context, _ = desktop_view
    events = []
    agent = Assistant(desktop=desktop, publish_event=events.append)
    describe = agent._computer.describe

    def invalidate_during_encoding(*, include_image=True, frame=None):
        result = describe(include_image=include_image, frame=frame)
        if include_image:
            agent._computer.invalidate()
        return result

    agent._computer.describe = invalidate_during_encoding
    with pytest.raises(ToolError, match="not be delivered"):
        await agent.capture_screen(context)
    assert not any(event.get("phase") == "observed" for event in events)


async def test_browser_error_captures_current_screen_before_recovery_advice(
    desktop_view,
):
    desktop, _, context, _ = desktop_view
    events = []
    agent = Assistant(desktop=desktop, publish_event=events.append)

    def obscured():
        raise RuntimeError("control is obscured")

    with pytest.raises(ToolError, match="observation_id"):
        await agent._browser_call("select control", obscured, context=context)
    assert any(event.get("phase") == "observed" for event in events)
    assert agent._computer._frame is not None


@pytest.mark.parametrize(
    "error", [ScopeViolation("stopped"), HostActionTimeout(effect_started=True)]
)
async def test_browser_scope_or_uncertain_timeout_never_starts_visual_fallback(error):
    agent = Assistant()
    captures = []

    async def capture(_):
        captures.append(True)

    agent.capture_screen = capture

    async def run(*args, **kwargs):
        raise error

    agent._run_host_action = run
    with pytest.raises(type(error)):
        await agent._browser_call("click", lambda: None)
    assert captures == []


def test_desktop_key_and_focus_tools_are_in_actual_model_schema():
    names = {tool.info.name for tool in Assistant().tools}
    assert {
        "capture_screen",
        "inspect_screen",
        "focus_desktop_window",
        "press_desktop_key",
        "click_desktop_element",
        "type_on_desktop",
    } <= names


def test_stop_clears_preview_without_reusing_last_observation(desktop_view):
    events = []
    agent = Assistant(desktop=desktop_view[0], publish_event=events.append)
    agent._stop_host_tasks()
    assert events[-1]["type"] == "computer.observation"
    assert events[-1]["phase"] == "cleared"
    assert "screenshot_data_url" not in events[-1]


async def test_session_exit_closes_preview_publisher_after_final_clear(desktop_view):
    events = []

    class Publisher:
        def __call__(self, event):
            events.append(event["phase"])

        async def aclose(self):
            events.append("closed")

    browser = SimpleNamespace(close=lambda: None)
    agent = Assistant(
        desktop=desktop_view[0], browser=browser, publish_event=Publisher()
    )
    await agent.on_exit()
    assert events == ["cleared", "closed"]
