"""Image-to-input regression: mock only the physical desktop boundary."""

import io
import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from livekit import rtc
from livekit.agents.utils import images
from PIL import Image

from desktop import DesktopController


@pytest.fixture
def observed(tmp_path):
    from computer_use import ObservedDesktop

    position = [100, 100]
    clicks = []
    window = SimpleNamespace(
        title="Chrome profiles", left=0, top=0, width=1920, height=1080
    )
    backend = SimpleNamespace(
        size=lambda: (1920, 1080),
        screenshot=lambda: Image.new("RGB", (1920, 1080), "#222222"),
        getActiveWindow=lambda: window,
        position=lambda: tuple(position),
        moveTo=lambda x, y, **kw: position.__setitem__(slice(None), [x, y]),
        click=lambda x, y, **kw: clicks.append((x, y, kw)),
        scroll=lambda amount: clicks.append(("scroll", amount)),
        hscroll=lambda amount: clicks.append(("horizontal", amount)),
        write=lambda text, **kw: clicks.append(("text", text)),
        mouseDown=lambda **kw: clicks.append(("down", kw)),
        mouseUp=lambda **kw: clicks.append(("up", kw)),
    )
    desktop = DesktopController(mouse=backend, capture_dir=tmp_path)
    computer = ObservedDesktop(desktop, indicator=lambda *args: nullcontext())
    return computer, backend, window, position, clicks


def test_image_coordinates_land_on_original_profile_center(observed):
    computer, _, _, _, clicks = observed
    frame = computer.observe()
    assert (frame.width, frame.height) == (1024, 576)
    fresh = computer.act(frame.id, "click", x=819, y=230)
    assert clicks == [(1536, 432, {"button": "left"})]
    assert fresh.id != frame.id


def test_provider_receives_exact_coordinate_canvas(observed, monkeypatch):
    from realtime_model import build_realtime_model

    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    computer, *_ = observed
    frame = computer.observe()
    wire_frame = rtc.VideoFrame(
        frame.width, frame.height, rtc.VideoBufferType.RGBA, frame.rgba_bytes
    )
    for model in build_realtime_model(voice="Aoede").models:
        encoded = images.encode(wire_frame, model._opts.image_encode_options)
        assert Image.open(io.BytesIO(encoded)).size == (1024, 576)


@pytest.mark.parametrize("mutation", ["move", "resize", "replace", "focus", "display"])
def test_changed_target_refuses_input(observed, mutation):
    computer, backend, window, _, clicks = observed
    frame = computer.observe()
    if mutation == "move":
        window.left = 100
    elif mutation == "resize":
        window.width = 1800
    elif mutation == "replace":
        backend.getActiveWindow = lambda: SimpleNamespace(**vars(window))
    elif mutation == "focus":
        window.title = "Another application"
    else:
        backend.size = lambda: (2560, 1440)
    with pytest.raises(ValueError, match=r"changed|foreground"):
        computer.act(frame.id, "click", x=819, y=230)
    assert clicks == []


def test_missing_replaced_and_consumed_observations_refuse_click(observed):
    computer, _, _, _, clicks = observed
    with pytest.raises(ValueError, match="capture_screen"):
        computer.act("invented", "click", x=400, y=200)
    old = computer.observe()
    frame = computer.observe()
    with pytest.raises(ValueError, match="capture_screen"):
        computer.act(old.id, "click", x=400, y=200)
    computer.act(frame.id, "click", x=400, y=200)
    with pytest.raises(ValueError, match="capture_screen"):
        computer.act(frame.id, "click", x=400, y=200)
    assert len(clicks) == 1


def test_expired_observation_refuses_click(observed, monkeypatch):
    import computer_use

    computer, _, _, _, clicks = observed
    monkeypatch.setattr(computer_use.time, "monotonic", lambda: 100)
    frame = computer.observe()
    monkeypatch.setattr(computer_use.time, "monotonic", lambda: 161)
    with pytest.raises(ValueError, match="capture_screen"):
        computer.act(frame.id, "click", x=400, y=200)
    assert clicks == []


@pytest.mark.parametrize(
    "x,y", [(1024, 30), (-1, 20), (10, 576), (True, 20), (10.2, 20)]
)
def test_invalid_image_coordinates_never_reach_native_input(observed, x, y):
    computer, _, _, _, clicks = observed
    frame = computer.observe()
    with pytest.raises(ValueError):
        computer.act(frame.id, "click", x=x, y=y)
    assert clicks == []


def test_point_outside_observed_window_is_refused(observed):
    computer, _, window, _, clicks = observed
    window.left, window.top, window.width, window.height = 800, 200, 500, 500
    frame = computer.observe()
    with pytest.raises(ValueError, match="outside"):
        computer.act(frame.id, "click", x=10, y=10)
    assert clicks == []


def test_scroll_positions_pointer_in_observed_pane(observed):
    computer, _, _, position, clicks = observed
    frame = computer.observe()
    computer.act(frame.id, "scroll", x=819, y=230, clicks=-3)
    assert position == [1536, 432]
    assert clicks == [("scroll", -3)]


def test_input_failure_consumes_observation_and_cleans_indicator(observed):
    from contextlib import contextmanager

    computer, backend, _, _, _ = observed
    indicator = []

    @contextmanager
    def visible(*args):
        indicator.append("show")
        try:
            yield
        finally:
            indicator.append("hide")

    computer.indicator = visible
    backend.click = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("input failed"))
    frame = computer.observe()
    with pytest.raises(RuntimeError, match="input failed"):
        computer.act(frame.id, "click", x=819, y=230)
    with pytest.raises(ValueError, match="capture_screen"):
        computer.act(frame.id, "click", x=819, y=230)
    assert indicator == ["show", "hide"]


def test_failed_post_capture_does_not_claim_action_success(observed):
    computer, backend, _, _, clicks = observed
    frame = computer.observe()
    backend.screenshot = lambda: (_ for _ in ()).throw(RuntimeError("capture failed"))
    with pytest.raises(RuntimeError, match=r"Input was sent.*not verified"):
        computer.act(frame.id, "click", x=819, y=230)
    assert len(clicks) == 1


async def test_voice_capture_click_returns_new_image_and_maps_native_point(
    observed, tmp_path, monkeypatch
):
    from agent import Assistant
    from skill_store import SkillStore

    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    computer, _, _, _, clicks = observed
    assistant = Assistant(
        desktop=computer.desktop, skills=SkillStore(tmp_path / "skills")
    )
    frames = []
    context = SimpleNamespace(
        session=SimpleNamespace(
            _activity=SimpleNamespace(
                _started=True, _rt_session=object(), push_video=frames.append
            )
        )
    )
    result = json.loads(await assistant.capture_screen(context))
    assert result["coordinate_space"] == "image_pixels"
    assert result["image_size"] == [1024, 576]
    from livekit.agents import llm

    def attached_images():
        return [
            c.image
            for m in assistant.chat_ctx.messages()
            for c in m.content
            if isinstance(c, llm.ImageContent)
        ]

    assert (attached_images()[0].width, attached_images()[0].height) == (1024, 576)
    after = json.loads(
        await assistant.click_mouse(
            context, 819, 230, observation_id=result["observation_id"]
        )
    )
    assert clicks == [(1536, 432, {"button": "left"})]
    assert after["observation_id"] != result["observation_id"]
    assert len(attached_images()) == 2


async def test_voice_refuses_coordinates_when_no_image_reached_model(
    observed, tmp_path, monkeypatch
):
    from livekit.agents import ToolError

    from agent import Assistant
    from skill_store import SkillStore

    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    computer, _, _, _, clicks = observed
    assistant = Assistant(
        desktop=computer.desktop, skills=SkillStore(tmp_path / "skills")
    )
    with pytest.raises(ToolError, match=r"image|screen"):
        await assistant.capture_screen(None)
    assert clicks == []


def test_invalid_scroll_does_not_even_move_pointer(observed):
    computer, _, _, position, clicks = observed
    frame = computer.observe()
    with pytest.raises(ValueError):
        computer.act(frame.id, "scroll", x=819, y=230, clicks=0)
    assert position == [100, 100]
    assert clicks == []


@pytest.mark.parametrize(
    "interruption",
    ["disconnect", "host_action", "skill_action", "model_swap", "swapping"],
)
async def test_voice_refuses_old_image_after_other_activity(
    observed, tmp_path, monkeypatch, interruption
):
    from livekit.agents import ToolError

    from agent import Assistant
    from skill_store import SkillStore

    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    computer, _, _, _, clicks = observed
    assistant = Assistant(
        desktop=computer.desktop, skills=SkillStore(tmp_path / "skills")
    )
    activity = SimpleNamespace(
        _started=True,
        _rt_session=SimpleNamespace(_active=object(), _swapping=False),
        push_video=lambda _: None,
    )
    context = SimpleNamespace(session=SimpleNamespace(_activity=activity))
    result = json.loads(await assistant.capture_screen(context))
    if interruption == "disconnect":
        activity._rt_session = None
    elif interruption == "model_swap":
        activity._rt_session._active = object()
    elif interruption == "swapping":
        activity._rt_session._swapping = True
    elif interruption == "host_action":
        await assistant._run_host_action(lambda: None)
    else:
        await assistant._get_skill_runtime()
        # Real saved-skill adapter, same foreground/title after input.
        outcome = await assistant._skill_registry.call(
            "desktop_click", {"x": 500, "y": 300, "window_title": "Chrome profiles"}
        )
        assert outcome.ok
        clicks.clear()
    with pytest.raises(ToolError, match=r"capture_screen|image"):
        await assistant.click_mouse(
            context, 819, 230, observation_id=result["observation_id"]
        )
    assert clicks == []


async def test_image_and_observation_id_share_a_discrete_model_message(
    observed, tmp_path, monkeypatch
):
    from livekit.agents import llm

    from agent import Assistant
    from skill_store import SkillStore

    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    computer, *_ = observed
    assistant = Assistant(
        desktop=computer.desktop, skills=SkillStore(tmp_path / "skills")
    )
    context = SimpleNamespace(
        session=SimpleNamespace(
            _activity=SimpleNamespace(
                _started=True, _rt_session=object(), push_video=lambda _: None
            )
        )
    )
    result = json.loads(await assistant.capture_screen(context))
    messages = list(assistant.chat_ctx.messages())
    image_messages = [
        m for m in messages if any(isinstance(c, llm.ImageContent) for c in m.content)
    ]
    assert len(image_messages) == 1
    assert result["observation_id"] in image_messages[0].text_content
    wire, _ = llm.ChatContext(image_messages).to_provider_format(
        format="google", inject_dummy_user_message=False
    )
    parts = wire[0]["parts"]
    assert any("inline_data" in part for part in parts)


def test_accessibility_id_clicks_named_control_and_rejects_layout_drift(observed):
    computer, backend, _, _, clicks = observed
    controls = [
        {
            "identity": "profile-1",
            "name": "Guest",
            "role": "Button",
            "bounds": [760, 240, 360, 290],
        },
        {
            "identity": "profile-3",
            "name": "kommidi",
            "role": "Button",
            "bounds": [1240, 240, 360, 290],
        },
    ]
    backend.get_accessible_elements = lambda window: controls.copy()
    frame = computer.observe()
    target = next(t for t in frame.targets if t.name == "kommidi")
    computer.act(frame.id, "click_target", target_id=target.id)
    assert clicks == [(1420, 385, {"button": "left"})]
    frame = computer.observe()
    controls[1] = {**controls[1], "bounds": [760, 240, 360, 290]}
    with pytest.raises(ValueError, match=r"changed|capture_screen"):
        computer.act(frame.id, "click_target", target_id=frame.targets[1].id)
    assert len(clicks) == 1


def test_accessibility_id_cannot_be_reused_with_a_different_frame(observed):
    computer, backend, _, _, clicks = observed
    backend.get_accessible_elements = lambda window: [
        {
            "identity": "p3",
            "name": "kommidi",
            "role": "Button",
            "bounds": [1240, 240, 360, 290],
        }
    ]
    old = computer.observe()
    fresh = computer.observe()
    with pytest.raises(ValueError, match=r"target|capture_screen"):
        computer.act(fresh.id, "click_target", target_id=old.targets[0].id)
    assert clicks == []


def test_named_control_drift_during_indicator_start_refuses_click(observed):
    from contextlib import contextmanager

    computer, backend, _, _, clicks = observed
    control = {
        "identity": "p3",
        "name": "kommidi",
        "role": "Button",
        "bounds": [1240, 240, 360, 290],
    }
    backend.get_accessible_elements = lambda _: [control.copy()]

    @contextmanager
    def changed(*_):
        control["bounds"] = [760, 240, 360, 290]
        yield

    computer.indicator = changed
    frame = computer.observe()
    with pytest.raises(ValueError, match="changed"):
        computer.act(frame.id, "click_target", target_id=frame.targets[0].id)
    assert clicks == []


def test_post_click_window_transition_recaptures_without_replaying_input(observed):
    computer, backend, window, _, clicks = observed
    frame = computer.observe()
    screenshot = backend.screenshot

    def capture_transition():
        window.title = "Opened kommidi"
        return screenshot()

    backend.screenshot = capture_transition
    after = computer.act(frame.id, "click", x=819, y=230)
    assert after.window.title == "Opened kommidi"
    assert len(clicks) == 1
