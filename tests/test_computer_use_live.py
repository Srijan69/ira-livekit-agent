"""Opt-in Gemini visual targeting through real IRA tools; no personal desktop."""

import asyncio
import os
from types import SimpleNamespace

import pytest
from livekit.agents import AgentSession, ToolError, room_io
from livekit.agents.voice.run_result import mock_tools
from PIL import Image, ImageDraw, ImageFont

from agent import Assistant, _scoped_text_input
from desktop import DesktopController
from dual_memory import DualMemoryStore
from request_scope import ScopeViolation
from skill_store import SkillStore

pytestmark = pytest.mark.skipif(
    os.getenv("IRA_RUN_LIVE_EVALS") != "1" or not os.getenv("GOOGLE_API_KEY"),
    reason="opt-in Gemini visual targeting with an owned synthetic image",
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


async def test_gemini_selects_top_kommidi_from_image_using_real_tools(
    tmp_path, monkeypatch
):
    import io

    from google.genai.live import AsyncSession

    wire_images = []
    send = AsyncSession.send_client_content

    async def inspect_send(self, *, turns=None, turn_complete=True):
        for turn in turns or []:
            for part in turn.parts or []:
                if part.inline_data:
                    wire_images.append(
                        Image.open(io.BytesIO(part.inline_data.data)).size
                    )
                    if len(wire_images) == 1:
                        (tmp_path / "model-view.jpg").write_bytes(part.inline_data.data)
                        print("model image:", tmp_path / "model-view.jpg")
        return await send(self, turns=turns, turn_complete=turn_complete)

    monkeypatch.setattr(AsyncSession, "send_client_content", inspect_send)
    selected = []
    tiles = [
        ("Personal", (280, 240, 640, 530)),
        ("Guest", (760, 240, 1120, 530)),
        ("kommidi", (1240, 240, 1600, 530)),
        ("kommidi work", (760, 630, 1120, 920)),
    ]
    font = ImageFont.load_default(size=38)

    def screenshot():
        image = Image.new("RGB", (1920, 1080), "#202124")
        draw = ImageDraw.Draw(image)
        draw.text((600, 95), "Who's using Chrome?", font=font, fill="white")
        if selected:
            draw.text(
                (480, 450),
                "Opened profile: " + selected[-1][0],
                font=font,
                fill="white",
            )
        else:
            for name, box in tiles:
                draw.rounded_rectangle(
                    box, radius=18, fill="#303134", outline="#9aa0a6", width=3
                )
                x, y = box[0], box[1]
                draw.ellipse((x + 130, y + 50, x + 230, y + 150), fill="#8ab4f8")
                draw.text((x + 35, y + 210), name, font=font, fill="white")
        return image

    def click(x, y, **kwargs):
        name = next(
            (
                name
                for name, (left, top, right, bottom) in tiles
                if left <= x < right and top <= y < bottom
            ),
            "MISSED",
        )
        selected.append((name, x, y))

    window = SimpleNamespace(
        title="Chrome profiles", left=0, top=0, width=1920, height=1080
    )
    backend = SimpleNamespace(
        size=lambda: (1920, 1080),
        screenshot=screenshot,
        getActiveWindow=lambda: window,
        click=click,
        get_accessible_elements=lambda _: (
            [
                {
                    "identity": "result",
                    "name": "Opened profile: " + selected[-1][0],
                    "role": "Text",
                    "bounds": [480, 450, 900, 100],
                }
            ]
            if selected
            else [
                {
                    "identity": name,
                    "name": name,
                    "role": "Button",
                    "bounds": [left, top, right - left, bottom - top],
                }
                for name, (left, top, right, bottom) in tiles
            ]
        ),
    )
    assistant = Assistant(
        desktop=DesktopController(mouse=backend, capture_dir=tmp_path),
        skills=SkillStore(tmp_path / "skills"),
        memory=DualMemoryStore(tmp_path / "memories"),
    )

    async def unused(**kwargs):
        raise ToolError(
            "This owned fixture supports only capture_screen, click_mouse and click_desktop_element."
        )

    blocked = {
        tool.info.name: unused
        for tool in assistant.tools
        if tool.info.name
        not in {
            "capture_screen",
            "inspect_screen",
            "click_mouse",
            "click_desktop_element",
            "finish_request",
            "clarify_request",
        }
    }
    with mock_tools(Assistant, blocked):
        async with AgentSession() as session:
            await session.start(assistant)
            await _scoped_text_input(
                session,
                room_io.TextInputEvent(
                    text=(
                        "The Chrome profile picker is already open. Use capture_screen, then click "
                        "the profile whose name is exactly kommidi at the top. Check the resulting "
                        "image and tell me which profile opened. Do not open another app."
                    )
                ),
            )
            # SDK idle excludes detached background tools and the gap before an
            # automatic realtime reply. Wait for the actual request to close.
            await asyncio.wait_for(
                wait_for_request_closed(assistant._request_scope), 90
            )
            called = [
                i.name for i in session.history.items if i.type == "function_call"
            ]
            assert "capture_screen" in called
            assert "click_desktop_element" in called
            print(
                {
                    "wire_images": wire_images,
                    "events": [
                        i
                        for i in session.history.to_dict()["items"]
                        if i["type"] in {"function_call", "message"}
                    ],
                }
            )
    assert len(selected) == 1, selected
    assert selected[0][0] == "kommidi", selected
    print(f"Gemini selected {selected[0][0]} at native pixels {selected[0][1:]}")
