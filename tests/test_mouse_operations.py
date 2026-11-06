"""Actual controller dispatch with an injected physical-input boundary."""

import asyncio
import json
import sys
import threading
from types import SimpleNamespace

import pytest
from livekit.agents import ToolError
from PIL import Image

from agent import Assistant
from desktop import DesktopController
from skill_adapters import SkillToolAdapters
from skill_execution import SkillExecutionService
from skill_format import Action, Check, Skill, SkillInput, Step
from skill_runs import SkillRunStore
from skill_store import SkillStore
from telemetry import SessionStatusReporter


@pytest.fixture
def mouse():
    events = []
    position = [20, 20]
    window = SimpleNamespace(title="Fixture", left=0, top=0, width=800, height=600)

    def move(x, y, **kwargs):
        position[:] = [x, y]
        events.append(("move", x, y, kwargs))

    backend = SimpleNamespace(
        size=lambda: (800, 600),
        screenshot=lambda: Image.new("RGB", (800, 600)),
        position=lambda: tuple(position),
        getActiveWindow=lambda: window,
        moveTo=move,
        click=lambda *args, **kwargs: events.append(("click", args, kwargs)),
        mouseDown=lambda **kwargs: events.append(("down", kwargs)),
        mouseUp=lambda **kwargs: events.append(("up", kwargs)),
        scroll=lambda amount: events.append(("vertical", amount)),
        hscroll=lambda amount: events.append(("horizontal", amount)),
    )
    return DesktopController(mouse=backend), backend, events


def test_double_click_and_both_scroll_axes_use_requested_arguments(mouse):
    desktop, _, events = mouse
    desktop.click(40, 50, button="right", clicks=2)
    desktop.scroll(-4)
    desktop.scroll(3, horizontal=True)
    assert events == [
        ("click", (40, 50), {"button": "right", "clicks": 2, "interval": 0.1}),
        ("vertical", -4),
        ("horizontal", 3),
    ]


def test_drag_validates_both_endpoints_before_moving_and_releases_on_error(mouse):
    desktop, backend, events = mouse
    with pytest.raises(ValueError):
        desktop.drag(10, 10, 900, 20)
    assert events == []

    def fail_at_destination(x, y, **kwargs):
        events.append(("move", x, y))
        if x == 100:
            raise RuntimeError("input failure")

    backend.moveTo = fail_at_destination
    with pytest.raises(RuntimeError, match="input failure"):
        desktop.drag(10, 10, 100, 120, button="middle")
    assert events == [
        ("move", 10, 10),
        ("down", {"button": "middle"}),
        ("move", 100, 120),
        ("up", {"button": "middle"}),
    ]


def test_real_pyautogui_failsafe_still_releases_drag_button(monkeypatch):
    import pyautogui

    position = [20, 20]
    held = set()
    events = []

    def release(x, y, button):
        held.discard(button)
        events.append(("release", x, y, button))

    # Replace the entire physical backend: every function above this boundary
    # is the installed PyAutoGUI implementation, but no OS input is possible.
    monkeypatch.setattr(
        pyautogui,
        "platformModule",
        SimpleNamespace(
            _size=lambda: (800, 600),
            _position=lambda: tuple(position),
            _moveTo=lambda x, y: position.__setitem__(slice(None), [x, y]),
            _mouseDown=lambda x, y, button: held.add(button),
            _mouseUp=release,
        ),
    )
    monkeypatch.setattr(pyautogui, "FAILSAFE", True)
    monkeypatch.setattr(pyautogui, "PAUSE", 0)
    with pytest.raises(pyautogui.FailSafeException):
        DesktopController(mouse=pyautogui).drag(10, 10, 0, 0, duration=0.1)
    assert held == set()
    assert events == [("release", 0, 0, "left")]
    assert pyautogui.FAILSAFE is True


@pytest.mark.skipif(sys.platform != "win32", reason="Windows wheel event boundary")
def test_windows_pyautogui_scroll_sends_correct_axis_and_full_wheel_notches(
    monkeypatch,
):
    import pyautogui

    events = []
    backend = pyautogui.platformModule
    monkeypatch.setattr(
        backend,
        "_sendMouseEvent",
        lambda flag, x, y, dwData=0: events.append((flag, x, y, dwData)),  # noqa: N803 - PyAutoGUI API name
    )
    monkeypatch.setattr(backend, "_position", lambda: (20, 20))
    monkeypatch.setattr(backend, "_size", lambda: (800, 600))
    monkeypatch.setattr(pyautogui, "PAUSE", 0)
    monkeypatch.setattr(pyautogui, "FAILSAFE", True)
    desktop = DesktopController(mouse=pyautogui)
    desktop.scroll(-3)
    desktop.scroll(2, horizontal=True)
    assert events == [(0x0800, 20, 20, -360), (0x1000, 20, 20, 240)]
    monkeypatch.setattr(backend, "_position", lambda: (0, 0))
    with pytest.raises(pyautogui.FailSafeException):
        desktop.scroll(2, horizontal=True)
    assert len(events) == 2


@pytest.mark.parametrize("amount", [True, 0, 101, -101, 1.5])
def test_invalid_scroll_never_touches_backend(mouse, amount):
    desktop, _, events = mouse
    with pytest.raises(ValueError):
        desktop.scroll(amount)
    assert events == []


async def test_voice_dispatches_mouse_actions_and_reports_operational_failures(
    tmp_path, monkeypatch, mouse, desktop_view
):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    desktop, backend, events = mouse
    assistant = Assistant(skills=SkillStore(tmp_path), desktop=desktop)
    context = desktop_view[2]
    frame = json.loads(await assistant.capture_screen(context))
    frame = json.loads(
        await assistant.scroll_mouse(context, -3, 20, 20, frame["observation_id"])
    )
    frame = json.loads(
        await assistant.drag_mouse(context, 10, 20, 50, 60, frame["observation_id"])
    )
    frame = json.loads(
        await assistant.click_mouse(context, 50, 60, frame["observation_id"], clicks=2)
    )
    assert ("vertical", -3) in events
    assert events[-1] == (
        "click",
        (50, 60),
        {"button": "left", "clicks": 2, "interval": 0.1},
    )
    backend.hscroll = lambda _: (_ for _ in ()).throw(RuntimeError("wheel unavailable"))
    with pytest.raises(ToolError):
        await assistant.scroll_mouse(
            context, 2, 50, 60, frame["observation_id"], horizontal=True
        )


async def test_compiled_mouse_actions_are_guarded_and_have_pointer_observation(mouse):
    desktop, _, events = mouse
    adapters = SkillToolAdapters(None, desktop, None)
    registry = adapters.registry()
    assert {"desktop_move", "desktop_scroll", "desktop_drag"} <= registry.names()
    result = await registry.call(
        "desktop_move", {"x": 30, "y": 40, "window_title": "Fixture"}
    )
    assert result.ok
    assert adapters.verify(Check("pointer_position", '{"x":30,"y":40}')).ok
    with pytest.raises(ValueError, match="foreground"):
        await registry.call("desktop_scroll", {"clicks": 2, "window_title": "Wrong"})
    assert not any(event[0] == "vertical" for event in events)


async def test_cancelled_drag_retains_host_ownership_until_button_is_released(
    tmp_path, monkeypatch, mouse, desktop_view
):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    desktop, backend, events = mouse
    dragging, release = threading.Event(), threading.Event()
    move = backend.moveTo

    def blocked_move(x, y, **kwargs):
        move(x, y, **kwargs)
        if kwargs.get("duration"):
            dragging.set()
            assert release.wait(3)

    backend.moveTo = blocked_move
    assistant = Assistant(skills=SkillStore(tmp_path), desktop=desktop)
    context = desktop_view[2]
    frame = json.loads(await assistant.capture_screen(context))
    drag = asyncio.create_task(
        assistant.drag_mouse(context, 10, 10, 100, 100, frame["observation_id"])
    )
    scroll = None
    try:
        assert await asyncio.to_thread(dragging.wait, 2)
        drag.cancel()
        with pytest.raises(asyncio.CancelledError):
            await drag
        assert assistant._execution_lock.locked()

        async def observe_and_scroll():
            frame = json.loads(await assistant.capture_screen(context))
            return await assistant.scroll_mouse(
                context, -2, 100, 100, frame["observation_id"]
            )

        scroll = asyncio.create_task(observe_and_scroll())
        await asyncio.sleep(0.03)
        assert not scroll.done()
        release.set()
        await asyncio.wait_for(scroll, 2)
        assert events[-3][0] == "up"
        assert events[-1] == ("vertical", -2)
        assert not assistant._execution_lock.locked()
    finally:
        release.set()
        await asyncio.gather(
            drag, *([scroll] if scroll else []), return_exceptions=True
        )


@pytest.mark.parametrize(
    "tool,args",
    [
        (
            "desktop_move",
            {"x": "{x}", "y": -(2**31) - 1, "window_title": "Fixture"},
        ),
        (
            "desktop_scroll",
            {"clicks": "{amount}", "window_title": "", "horizontal": False},
        ),
        (
            "desktop_drag",
            {
                "start_x": "{x}",
                "start_y": 10,
                "end_x": 50,
                "end_y": 50,
                "window_title": "Fixture",
                "duration": 0,
            },
        ),
    ],
)
def test_symbolic_mouse_arguments_do_not_hide_invalid_constants(mouse, tool, args):
    desktop, _, events = mouse
    with pytest.raises(ValueError):
        SkillToolAdapters(None, desktop, None).registry().validate(
            tool, args, symbolic=True
        )
    assert events == []


@pytest.mark.parametrize(
    "y,expected_status", [(40, "waiting"), (-(2**31) - 1, "error")]
)
async def test_pointer_check_substitutes_integer_answers_and_preflights_constants(
    tmp_path, mouse, y, expected_status
):
    desktop, _, events = mouse
    adapters = SkillToolAdapters(None, desktop, None)
    service = SkillExecutionService(
        verifier=adapters.verify,
        check_validator=adapters.validate_check,
        run_store=SkillRunStore(tmp_path / "runs.db"),
    )
    skill = Skill(
        "Move pointer",
        "Observe cursor after movement",
        state="enabled",
        inputs=(SkillInput("x", "X coordinate"),),
        allowed_tools=("desktop_move",),
        steps=(
            Step(1, "Ask", action=Action("ask", param="x", text="Which X coordinate?")),
            Step(
                2,
                "Move",
                action=Action(
                    "tool",
                    tool="desktop_move",
                    args=(
                        ("x", "{x}"),
                        ("y", "40"),
                        ("window_title", "Fixture"),
                    ),
                ),
                check=Check("pointer_position", '{"x":"{x}","y":' + str(y) + "}"),
            ),
        ),
    )
    try:
        result = await service.run(
            skill, adapters.registry(), SessionStatusReporter(sink=lambda _: None)
        )
        assert result.status == expected_status
        assert events == []
        if result.status == "waiting":
            with pytest.raises(ValueError):
                await service.answer(
                    result.run_id, "true", question_id=result.question_id
                )
            assert events == []
            done = await service.answer(
                result.run_id, "30", question_id=result.question_id
            )
            assert done.status == "done", done.message
            assert desktop.pointer_position() == (30, 40)
    finally:
        await service.aclose()
