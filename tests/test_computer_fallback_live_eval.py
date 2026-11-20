"""Real Gemini/LiveKit decisions over an isolated browser obstruction fixture."""

import asyncio
import json
import os
from types import SimpleNamespace

import pytest
from livekit.agents import AgentSession
from PIL import Image, ImageDraw, ImageFont

from agent import Assistant
from browser import PageElement
from desktop import DesktopController
from dual_memory import DualMemoryStore
from skill_store import SkillStore
from skills import SkillCatalog

pytestmark = pytest.mark.skipif(
    os.environ.get("IRA_RUN_LIVE_EVALS") != "1" or not os.environ.get("GOOGLE_API_KEY"),
    reason="Live Gemini computer-use routing evaluation is opt-in",
)


async def test_live_model_recovers_browser_obstruction_with_observed_desktop(tmp_path):
    effects = []
    events = []
    pointer = [0, 0]
    window = SimpleNamespace(
        title="Brave - IRA test page", left=0, top=0, width=800, height=600
    )

    def screen():
        image = Image.new("RGB", (800, 600), "#f2f4f7")
        draw = ImageDraw.Draw(image)
        draw.text((60, 50), "IRA browser recovery evaluation", fill="black")
        complete = "clicked" in effects
        draw.rectangle((200, 180, 400, 240), fill="#226d38" if complete else "#1565c0")
        draw.text(
            (230, 195),
            "Done" if complete else "Continue",
            fill="white",
            font=ImageFont.load_default(size=26),
        )
        if complete:
            draw.text(
                (60, 300),
                "Completed: Continue was clicked.",
                fill="black",
                font=ImageFont.load_default(size=24),
            )
        return image

    def click(x, y, **kwargs):
        # Only clicks on the observed Continue target can satisfy the fixture.
        pointer[:] = [x, y]
        effects.append(
            "clicked" if 200 <= x <= 400 and 180 <= y <= 240 else "wrong_target"
        )

    def move_to(x, y, **kwargs):
        pointer[:] = [x, y]

    backend = SimpleNamespace(
        size=lambda: (800, 600),
        screenshot=screen,
        getActiveWindow=lambda: window,
        getAllWindows=lambda: [window],
        moveTo=move_to,
        click=click,
    )
    desktop = DesktopController(mouse=backend, capture_dir=tmp_path / "captures")
    desktop.accessible_elements = lambda _window: (
        []
        if "clicked" in effects
        else [
            {
                "identity": "continue-button",
                "name": "Continue",
                "role": "button",
                "bounds": [200, 180, 200, 60],
            }
        ]
    )

    class Browser:
        current_url = "https://example.com/ira-evaluation"
        is_open = True

        def navigate(self, url):
            return "IRA test page. Continue is a button."

        def list_elements(self):
            return [PageElement(1, "button", "Continue")]

        def read_page(self):
            return (
                "IRA test page. Done: Continue was clicked."
                if "clicked" in effects
                else "IRA test page. Continue is a button."
            )

        def click(self, index):
            raise RuntimeError(
                "Playwright control is obscured by native browser UI; no click was sent."
            )

        def invalidate_elements(self):
            pass

        def close(self):
            return "Browser fixture closed."

    agent = Assistant(
        browser=Browser(),
        desktop=desktop,
        dual_memory=DualMemoryStore(tmp_path / "memory"),
        skills=SkillStore(tmp_path / "workflows"),
        skills_catalog=SkillCatalog(),
        publish_event=events.append,
    )
    allowed = {
        "browse_to",
        "read_page",
        "list_page_elements",
        "click_element",
        "capture_screen",
        "click_mouse",
        "click_desktop_element",
        "inspect_screen",
        "focus_desktop_window",
        "finish_request",
        "clarify_request",
    }
    agent._tools = [tool for tool in agent.tools if tool.info.name in allowed]
    run_host = agent._run_host_action

    async def isolated_host(fn, *args, **kwargs):
        if getattr(fn, "__self__", None) not in {
            agent._browser,
            agent._computer,
            agent,
        }:
            raise RuntimeError("Host operation outside this isolated fixture")
        return await run_host(fn, *args, **kwargs)

    agent._run_host_action = isolated_host
    text = "Using the browser, open https://example.com/ira-evaluation in Brave and click Continue."
    request = agent._request_scope.begin_request(
        text, source_id="evaluation:human:computer"
    )
    async with AgentSession() as session:
        await session.start(agent)

        def bind(event):
            if event.user_initiated:
                agent._scoped_tools.bind_speech(event.speech_handle, request)

        session.on("speech_created", bind)
        async with asyncio.timeout(75):
            result = await session.run(user_input=text)
        calls = [
            (event.item.name, json.loads(event.item.arguments))
            for event in result.events
            if event.type == "function_call"
        ]
    assert effects == ["clicked"], calls
    assert any(name == "click_element" for name, _ in calls), calls
    assert any(name in {"click_desktop_element", "click_mouse"} for name, _ in calls), (
        calls
    )
    assert any(name == "finish_request" for name, _ in calls), calls
    previews = [event for event in events if event["type"] == "computer.observation"]
    phases = [event["phase"] for event in previews]
    before_index = phases.index("before")
    assert "observed" in phases[:before_index], phases
    assert phases[before_index + 1] == "after", phases
    before, after = previews[before_index : before_index + 2]
    assert before["action"]["observation_id"] == before["observation_id"]
    assert before["observation_id"] != after["observation_id"]
    assert "action" not in after
    print(
        json.dumps(
            {
                "calls": [name for name, _ in calls],
                "effects": effects,
                "preview_phases": phases,
            }
        )
    )
