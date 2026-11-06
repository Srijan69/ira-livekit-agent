"""Opt-in Gemini tool selection with a harmless desktop boundary."""

import asyncio
import os

import pytest
from livekit.agents import AgentSession, ToolError, room_io
from livekit.agents.voice.run_result import mock_tools

from agent import Assistant, _scoped_text_input
from dual_memory import DualMemoryStore
from request_scope import ScopeViolation
from skill_store import SkillStore

pytestmark = pytest.mark.skipif(
    os.getenv("IRA_RUN_LIVE_EVALS") != "1" or not os.getenv("GOOGLE_API_KEY"),
    reason="opt-in Gemini tool selection with harmless tool bodies",
)


async def wait_for_request_closed(scope):
    request = scope.current_request
    while True:
        try:
            scope.require(request.id)
        except ScopeViolation as exc:
            assert "Request closed" in str(exc), str(exc)
            return
        await asyncio.sleep(0.05)


async def test_gemini_selects_mouse_tools_from_startup_without_capability_update(
    tmp_path,
    desktop_view,
):
    desktop, backend, _, _ = desktop_view
    calls = []
    position = [100, 100]

    def move(x, y, **kwargs):
        position[:] = [x, y]
        calls.append(("move", x, y))

    backend.moveTo = move
    backend.position = lambda: tuple(position)
    backend.scroll = lambda clicks: calls.append(("scroll", clicks, False))
    assistant = Assistant(
        desktop=desktop,
        skills=SkillStore(tmp_path / "skills"),
        memory=DualMemoryStore(tmp_path / "memories"),
    )

    async def unused(**kwargs):
        raise ToolError(
            "This isolated tool-selection fixture only supports mouse move and scroll."
        )

    mocked = {
        tool.info.name: unused
        for tool in assistant.tools
        if tool.info.name
        not in {
            "capture_screen",
            "move_mouse",
            "scroll_mouse",
            "finish_request",
            "clarify_request",
        }
    }
    with mock_tools(Assistant, mocked):
        async with AgentSession() as session:
            await session.start(assistant)
            await _scoped_text_input(
                session,
                room_io.TextInputEvent(
                    text="Capture the screen, then move the mouse cursor to image pixel x 120, y 160."
                ),
            )
            # SDK idle is allowed while tool bodies and auto replies are pending.
            # A second human turn must not supersede an unfinished first request.
            await asyncio.wait_for(
                wait_for_request_closed(assistant._request_scope), 90
            )
            await _scoped_text_input(
                session,
                room_io.TextInputEvent(
                    text="Scroll down three wheel notches at the current cursor."
                ),
            )
            await asyncio.wait_for(
                wait_for_request_closed(assistant._request_scope), 90
            )
    assert ("move", 120, 160) in calls
    assert ("scroll", -3, False) in calls
