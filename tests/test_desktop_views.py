"""Full capture, detailed image coordinates and observed window switching."""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from computer_use import ObservedDesktop
from desktop import DesktopController


@pytest.fixture
def view_desktop(tmp_path):
    events = []
    pointer = [100, 100]
    window = SimpleNamespace(title="Profiles", left=0, top=0, width=1920, height=1080)
    active = [window]
    windows = [window]
    bitmap = Image.new("RGB", (1920, 1080), "black")
    ImageDraw.Draw(bitmap).rectangle((750, 187, 900, 350), fill="red")
    backend = SimpleNamespace(
        size=lambda: (1920, 1080),
        screenshot=bitmap.copy,
        getActiveWindow=lambda: active[0],
        getAllWindows=lambda: windows[:],
        position=lambda: tuple(pointer),
        moveTo=lambda x, y, **kw: pointer.__setitem__(slice(None), [x, y]),
        click=lambda x, y, **kw: events.append(("click", x, y)),
    )
    computer = ObservedDesktop(
        DesktopController(mouse=backend, capture_dir=tmp_path),
        indicator=lambda *_: nullcontext(),
    )
    return computer, backend, active, windows, events, pointer


def test_detail_maps_to_original_pixels_and_retains_full_capture(view_desktop):
    computer, _, _, _, events, _ = view_desktop
    overview = computer.observe()
    detail = computer.detail(overview.id, x=400, y=100, width=400, height=300)
    assert detail.source_bounds == (750, 187, 750, 563)
    assert (detail.width, detail.height) == (750, 563)
    assert detail.capture.path == overview.capture.path
    assert Image.open(detail.capture.path).size == (1920, 1080)
    pixels = Image.frombytes("RGBA", (detail.width, detail.height), detail.rgba_bytes)
    assert pixels.getpixel((100, 100))[:3] == (255, 0, 0)
    computer.act(detail.id, "click", x=100, y=100)
    assert events == [("click", 850, 287)]


def test_detail_does_not_report_an_off_image_cursor(view_desktop):
    computer, _, _, _, _, pointer = view_desktop
    pointer[:] = [100, 100]
    overview = computer.observe()
    computer.detail(overview.id, x=400, y=100, width=400, height=300)
    assert computer.describe(include_image=False)["cursor_image_position"] is None


def test_detail_does_not_refresh_age_or_allow_old_ids(view_desktop, monkeypatch):
    import computer_use

    computer, _, _, _, events, _ = view_desktop
    clock = [100]
    monkeypatch.setattr(computer_use.time, "monotonic", lambda: clock[0])
    overview = computer.observe()
    clock[0] = 159
    detail = computer.detail(overview.id, x=0, y=0, width=400, height=300)
    with pytest.raises(ValueError, match=r"observation|capture_screen"):
        computer.act(overview.id, "click", x=100, y=100)
    clock[0] = 161
    with pytest.raises(ValueError, match=r"observation|capture_screen"):
        computer.act(detail.id, "click", x=100, y=100)
    assert events == []


@pytest.mark.parametrize(
    "rectangle", [(0, 0, 0, 30), (-1, 0, 20, 20), (1020, 0, 20, 20), (0, 0, True, 20)]
)
def test_invalid_detail_has_no_effect(view_desktop, rectangle):
    computer, _, _, _, events, _ = view_desktop
    frame = computer.observe()
    with pytest.raises(ValueError):
        computer.detail(frame.id, *rectangle)
    assert events == []


def test_move_only_can_cross_foreground_boundary(view_desktop):
    computer, _, active, _, events, pointer = view_desktop
    active[0].width = 500
    frame = computer.observe()
    computer.act(frame.id, "move", x=819, y=230)
    assert pointer == [1536, 432]
    assert events == []
    frame = computer.observe()
    with pytest.raises(ValueError, match=r"foreground|outside"):
        computer.act(frame.id, "click", x=819, y=230)


def test_focus_uses_fresh_window_id_then_recaptures(view_desktop):
    computer, _, active, windows, events, _ = view_desktop
    second = SimpleNamespace(title="Editor", left=600, top=100, width=800, height=700)

    def activate():
        events.append("focus")
        active[0] = second

    second.activate = activate
    windows.append(second)
    frame = computer.observe()
    target = next(w for w in frame.windows if w.window.title == "Editor")
    result = computer.focus(frame.id, target.id)
    assert events == ["focus"]
    assert result.window.title == "Editor"
    with pytest.raises(ValueError, match=r"observation|capture_screen"):
        computer.focus(frame.id, target.id)


def test_changed_window_target_refuses_focus(view_desktop):
    computer, _, active, windows, events, _ = view_desktop
    second = SimpleNamespace(
        title="Editor",
        left=600,
        top=100,
        width=800,
        height=700,
        activate=lambda: events.append("focus"),
    )
    windows.append(second)
    frame = computer.observe()
    target = next(w for w in frame.windows if w.window.title == "Editor")
    second.title = "Other app"
    with pytest.raises(ValueError, match=r"changed|capture_screen"):
        computer.focus(frame.id, target.id)
    assert active[0].title == "Profiles"
    assert events == []


def test_capture_files_are_bounded_and_owned_cleanup_preserves_other_files(
    view_desktop, tmp_path
):
    computer, *_ = view_desktop
    unrelated = tmp_path / "keep.png"
    unrelated.write_bytes(b"not an IRA capture")
    frames = [computer.observe() for _ in range(6)]
    assert sum(f.capture.path.exists() for f in frames) <= 3
    computer.close()
    assert not any(f.capture.path.exists() for f in frames)
    assert unrelated.exists()


def test_permit_revoked_during_indicator_prevents_input(view_desktop):
    from contextlib import contextmanager

    computer, _, _, _, events, _ = view_desktop
    revoked = [False]

    def check():
        if revoked[0]:
            raise PermissionError("task stopped")

    @contextmanager
    def indicator(*_):
        revoked[0] = True
        yield

    computer.indicator = indicator
    frame = computer.observe()
    with pytest.raises(PermissionError, match="stopped"):
        computer.act(frame.id, "click", x=300, y=200, _permit_check=check)
    assert events == []


def test_whole_desktop_view_maps_negative_origin_and_exposes_target_bounds(
    view_desktop,
):
    from desktop_layout import DesktopLayout, Monitor

    computer, backend, active, _, events, pointer = view_desktop
    layout = DesktopLayout(
        (
            Monitor("main", (0, 0, 1920, 1080), True),
            Monitor("left", (-1280, -200, 1280, 1024)),
        )
    )
    backend.get_monitor_layout = lambda: layout
    backend.screenshot = lambda: Image.new("RGB", (3200, 1280), "white")
    active[0].left, active[0].top = -1280, -200
    active[0].width, active[0].height = 1280, 1024
    pointer[:] = [-640, 200]
    backend.get_accessible_elements = lambda _: [
        {
            "identity": "profile",
            "name": "kommidi",
            "role": "Button",
            "bounds": [-740, 100, 200, 200],
        }
    ]
    frame = computer.observe()
    assert frame.source_bounds == (-1280, -200, 3200, 1280)
    assert (frame.width, frame.height) == (1024, 410)
    assert computer.describe(include_image=False)["targets"][0]["image_bounds"] == [
        173,
        96,
        64,
        64,
    ]
    assert computer.describe(include_image=False)["cursor_image_position"] == [204, 128]
    computer.act(frame.id, "click", x=204, y=128)
    assert events == [("click", -641, 201)]


def test_monitor_identity_change_with_same_bounds_rejects_input(view_desktop):
    from desktop_layout import DesktopLayout, Monitor

    computer, backend, _, _, events, _ = view_desktop
    frame = computer.observe()
    backend.get_monitor_layout = lambda: DesktopLayout(
        (Monitor("replaced", (0, 0, 1920, 1080), True),)
    )
    with pytest.raises(ValueError, match="changed"):
        computer.act(frame.id, "click", x=300, y=200)
    assert events == []


def test_cursor_at_virtual_desktop_edge_stays_inside_delivered_image(view_desktop):
    from desktop_layout import DesktopLayout, Monitor

    computer, backend, _, _, _, pointer = view_desktop
    backend.get_monitor_layout = lambda: DesktopLayout(
        (
            Monitor("main", (0, 0, 1920, 1080), True),
            Monitor("left", (-1280, -200, 1280, 1024)),
        )
    )
    backend.screenshot = lambda: Image.new("RGB", (3200, 1280), "white")
    pointer[:] = [1919, 1079]
    computer.observe()
    assert computer.describe(include_image=False)["cursor_image_position"] == [
        1023,
        409,
    ]


async def test_voice_detail_delivers_exact_crop_and_bounded_image_history(
    view_desktop, tmp_path, monkeypatch
):
    import json

    from livekit.agents import llm

    from agent import Assistant
    from skill_store import SkillStore

    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    computer, *_ = view_desktop
    assistant = Assistant(
        desktop=computer.desktop, skills=SkillStore(tmp_path / "skills")
    )
    context = SimpleNamespace(
        session=SimpleNamespace(
            _activity=SimpleNamespace(_started=True, _rt_session=object())
        )
    )
    ctx = assistant.chat_ctx.copy()
    human = ctx.add_message(role="user", content="Inspect the screen only")
    await assistant.update_chat_ctx(ctx)
    frame = json.loads(await assistant.capture_screen(context))
    detail = json.loads(
        await assistant.inspect_screen(
            context, frame["observation_id"], 400, 100, 400, 300
        )
    )
    assert detail["source_bounds"] == [750, 187, 750, 563]
    assert detail["image_size"] == [750, 563]
    for _ in range(5):
        await assistant.capture_screen(context)
    messages = assistant.chat_ctx.messages()
    assert any(m.id == human.id for m in messages)
    images = [
        c.image for m in messages for c in m.content if isinstance(c, llm.ImageContent)
    ]
    assert len(images) <= 4


async def test_frame_replaced_during_model_delivery_is_not_actionable(
    view_desktop, tmp_path, monkeypatch
):
    from livekit.agents import ToolError

    from agent import Assistant
    from skill_store import SkillStore

    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    computer, *_ = view_desktop
    assistant = Assistant(
        desktop=computer.desktop, skills=SkillStore(tmp_path / "skills")
    )
    context = SimpleNamespace(
        session=SimpleNamespace(
            _activity=SimpleNamespace(_started=True, _rt_session=object())
        )
    )
    original = assistant.update_chat_ctx

    async def interrupted(ctx):
        assistant._computer.observe()
        await original(ctx)

    monkeypatch.setattr(assistant, "update_chat_ctx", interrupted)
    with pytest.raises(ToolError, match=r"delivered|changed|available"):
        await assistant.capture_screen(context)
    assert assistant._computer._frame is None
