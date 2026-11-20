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


def test_control_drift_during_capture_is_not_published_as_observed(observed):
    from computer_use import DesktopChangedDuringCaptureError

    computer, backend, _, _, clicks = observed
    control = {
        "identity": "save",
        "name": "Save",
        "role": "Button",
        "bounds": [100, 100, 100, 40],
    }
    backend.get_accessible_elements = lambda _: [control.copy()]
    screenshot = backend.screenshot

    def changed_capture():
        control["bounds"] = [300, 100, 100, 40]
        return screenshot()

    backend.screenshot = changed_capture
    with pytest.raises(DesktopChangedDuringCaptureError, match="changed"):
        computer.observe()
    assert computer.describe(include_image=False) == {}
    assert clicks == []


def test_typing_refuses_changed_keyboard_field_in_same_window(observed):
    computer, backend, _, _, clicks = observed
    focus = ["search-field"]
    backend.get_keyboard_focus = lambda _: focus[0]
    frame = computer.observe()
    focus[0] = "message-field"
    with pytest.raises(ValueError, match=r"focus|changed"):
        computer.act(frame.id, "type", text="private input")
    assert clicks == []


def test_typing_refuses_changed_uia_focus_in_same_native_child(observed):
    computer, backend, _, _, clicks = observed
    backend.get_keyboard_focus = lambda _: 123
    controls = [
        {
            "identity": "search",
            "name": "Search",
            "role": "Edit",
            "bounds": [100, 100, 100, 40],
            "has_keyboard_focus": True,
        },
        {
            "identity": "message",
            "name": "Message",
            "role": "Edit",
            "bounds": [100, 200, 100, 40],
            "has_keyboard_focus": False,
        },
    ]
    backend.get_accessible_elements = lambda _: [c.copy() for c in controls]
    frame = computer.observe()
    controls[0]["has_keyboard_focus"] = False
    controls[1]["has_keyboard_focus"] = True
    with pytest.raises(ValueError, match=r"focus|changed"):
        computer.act(frame.id, "type", text="private input")
    assert clicks == []


def test_action_callback_marks_exact_target_in_pre_action_image(observed):
    computer, backend, _, _, clicks = observed
    backend.get_accessible_elements = lambda _: [
        {
            "identity": "p3",
            "name": "kommidi",
            "role": "Button",
            "bounds": [1240, 240, 360, 290],
        }
    ]
    frame = computer.observe()
    target = frame.targets[0]
    previews = []

    def preview(before, action):
        assert before is frame
        assert clicks == []
        previews.append(action)

    after = computer.act(
        frame.id, "click_target", target_id=target.id, _on_action=preview
    )
    assert previews == [
        {
            "action": "click_target",
            "observation_id": frame.id,
            "image_points": [[757, 205]],
            "target": {
                "id": target.id,
                "name": "kommidi",
                "role": "Button",
                "image_bounds": [661, 128, 192, 155],
            },
        }
    ]
    assert after.id != frame.id
    assert clicks == [(1420, 385, {"button": "left"})]


def test_action_callback_never_contains_typed_content(observed):
    computer, _, _, _, clicks = observed
    frame = computer.observe()
    previews = []
    computer.act(
        frame.id,
        "type",
        text="Private text",
        _on_action=lambda _, a: previews.append(a),
    )
    assert previews == [
        {"action": "type", "observation_id": frame.id, "image_points": []}
    ]
    assert (
        "".join(text for action, text in clicks if action == "text") == "Private text"
    )


def test_callback_revocation_stops_input_and_consumes_frame(observed):
    computer, _, _, _, clicks = observed
    frame = computer.observe()
    revoked = [False]

    def permit():
        if revoked[0]:
            raise PermissionError("stopped")

    with pytest.raises(PermissionError, match="stopped"):
        computer.act(
            frame.id,
            "click",
            x=300,
            y=200,
            _permit_check=permit,
            _on_action=lambda *_: revoked.__setitem__(0, True),
        )
    assert clicks == []
    assert computer.describe(include_image=False) == {}


def test_preview_failure_does_not_prevent_guarded_input(observed):
    computer, _, _, _, clicks = observed
    frame = computer.observe()

    def failed_preview(*_):
        raise OSError("dashboard disconnected")

    after = computer.act(frame.id, "click", x=300, y=200, _on_action=failed_preview)
    assert after.id != frame.id
    assert len(clicks) == 1


def test_describe_data_url_uses_exact_canvas_and_metadata_stays_small(observed):
    import base64

    computer, *_ = observed
    frame = computer.observe()
    meta = computer.describe(include_image=False)
    assert meta["captured_at"] == frame.capture.captured_at
    assert "screenshot_data_url" not in meta
    description = computer.describe()
    prefix, encoded = description["screenshot_data_url"].split(",", 1)
    assert prefix == "data:image/jpeg;base64"
    with Image.open(io.BytesIO(base64.b64decode(encoded))) as image:
        assert image.size == (1024, 576)
    assert len(encoded) < 450_000


def test_key_action_uses_fresh_focus_and_omits_key_from_preview(observed):
    computer, backend, _, _, effects = observed
    backend.press = lambda key: effects.append(("key", key))
    backend.get_keyboard_focus = lambda _: "owned-field"
    frame = computer.observe()
    previews = []
    result = computer.act(
        frame.id, "key", key="tab", _on_action=lambda _, a: previews.append(a)
    )
    assert effects == [("key", "tab")]
    assert previews == [
        {"action": "key", "observation_id": frame.id, "image_points": []}
    ]
    assert result.id != frame.id


@pytest.mark.parametrize("key", ["win", "ctrl", "a", "ctrl+a", "f5", ""])
def test_key_action_rejects_unverified_shortcuts_before_preview(observed, key):
    computer, backend, _, _, effects = observed
    backend.press = lambda key: effects.append(("key", key))
    frame = computer.observe()
    previews = []
    with pytest.raises(ValueError):
        computer.act(
            frame.id, "key", key=key, _on_action=lambda _, a: previews.append(a)
        )
    assert effects == []
    assert previews == []


def test_keyboard_focus_drift_during_preview_refuses_key(observed):
    computer, backend, _, _, effects = observed
    backend.press = lambda key: effects.append(("key", key))
    focus = ["owned-field"]
    backend.get_keyboard_focus = lambda _: focus[0]
    frame = computer.observe()
    with pytest.raises(ValueError, match=r"focus|changed"):
        computer.act(
            frame.id,
            "key",
            key="enter",
            _on_action=lambda *_: focus.__setitem__(0, "other-field"),
        )
    assert effects == []


def test_stopped_request_after_input_does_not_capture_or_replay(observed):
    computer, backend, _, _, effects = observed
    screenshots = []
    screenshot = backend.screenshot
    backend.screenshot = lambda: screenshots.append("capture") or screenshot()
    stopped = [False]

    def click(x, y, **kwargs):
        effects.append(("click", x, y))
        stopped[0] = True

    def permit():
        if stopped[0]:
            raise PermissionError("request stopped")

    backend.click = click
    frame = computer.observe()
    with pytest.raises(
        RuntimeError, match="Input was sent but the result is not verified"
    ):
        computer.act(frame.id, "click", x=300, y=200, _permit_check=permit)
    assert effects == [("click", 563, 375)]
    assert screenshots == ["capture"]
    assert computer.describe(include_image=False) == {}


def test_noisy_screenshot_is_bounded_without_changing_coordinate_canvas(observed):
    import base64
    import random

    computer, backend, _, _, _ = observed
    backend.size = lambda: (1024, 1024)
    pixels = random.Random(0).randbytes(1024 * 1024 * 3)
    backend.screenshot = lambda: Image.frombytes("RGB", (1024, 1024), pixels)
    computer.observe()
    description = computer.describe()
    encoded = description["screenshot_data_url"].split(",", 1)[1]
    image_bytes = base64.b64decode(encoded)
    assert len(image_bytes) <= 300_000
    with Image.open(io.BytesIO(image_bytes)) as image:
        assert image.size == (1024, 1024)


def test_action_marker_roundtrips_native_pixel_center_at_fractional_scale(observed):
    computer, backend, _, _, effects = observed
    backend.size = lambda: (1100, 600)
    backend.screenshot = lambda: Image.new("RGB", (1100, 600))
    frame = computer.observe()
    previews = []
    computer.act(
        frame.id, "click", x=1, y=1, _on_action=lambda _, a: previews.append(a)
    )
    assert effects == [(1, 1, {"button": "left"})]
    assert previews[0]["image_points"] == [[1, 1]]
