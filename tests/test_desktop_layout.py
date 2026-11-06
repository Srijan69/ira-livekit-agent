"""Physical monitor geometry and capture over controlled desktop boundaries."""

import sys
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest
from PIL import Image

import desktop as desktop_module
from desktop import DesktopController, DesktopUnavailableError, validate_coordinate
from desktop_layout import DesktopLayout, Monitor


def two_monitors():
    return DesktopLayout(
        (
            Monitor("primary", (0, 0, 1920, 1080), True),
            Monitor("left", (-1280, -200, 1280, 1024)),
        )
    )


def desktop_fixture(tmp_path, *, topology=None, image_size=(3200, 1280)):
    topology = topology or two_monitors()
    position = [-640, 200]
    events = []
    image = Image.new("RGB", image_size, "#102030")
    image.putpixel((0, 0), (11, 22, 33))
    image.putpixel((image_size[0] - 1, image_size[1] - 1), (44, 55, 66))

    def move(x, y, **kwargs):
        position[:] = [x, y]
        events.append(("move", x, y))

    backend = SimpleNamespace(
        size=lambda: (1920, 1080),
        get_monitor_layout=lambda: topology,
        screenshot=lambda: image,
        position=lambda: tuple(position),
        moveTo=move,
        click=lambda x, y, **kwargs: events.append(("click", x, y)),
        mouseDown=lambda **kwargs: events.append(("down", kwargs["button"])),
        mouseUp=lambda **kwargs: events.append(("up", kwargs["button"])),
        scroll=lambda amount: events.append(("scroll", amount)),
        hscroll=lambda amount: events.append(("hscroll", amount)),
    )
    return DesktopController(mouse=backend, capture_dir=tmp_path), backend, events


def test_virtual_bounds_use_monitor_extents_and_preserve_gaps():
    topology = two_monitors()
    assert topology.bounds == (-1280, -200, 3200, 1280)
    for point in [(-1280, -200), (-640, 200), (-1, 823), (1919, 1079)]:
        assert topology.contains(*point), point
    for point in [(-1281, 0), (-1000, 1000), (-1, 824), (0, -1), (1920, 0)]:
        assert not topology.contains(*point), point


def test_topology_is_immutable_and_enumeration_order_is_not_a_change():
    topology = two_monitors()
    assert topology == DesktopLayout(tuple(reversed(topology.monitors)))
    with pytest.raises(FrozenInstanceError):
        topology.monitors[0].bounds = (0, 0, 1, 1)
    with pytest.raises(ValueError):
        DesktopLayout(())
    with pytest.raises(ValueError):
        Monitor("broken", (0, 0, 0, 1080))
    with pytest.raises(ValueError):
        DesktopLayout((Monitor("same", (0, 0, 1, 1)), Monitor("same", (1, 0, 1, 1))))


def test_capture_preserves_full_pixels_origin_and_topology(tmp_path):
    controller, _, _ = desktop_fixture(tmp_path)
    capture = controller.capture_screen()
    assert capture.origin == (-1280, -200)
    assert capture.layout == two_monitors()
    assert (capture.width, capture.height) == (3200, 1280)
    assert capture.rgb_bytes[:3] == bytes([11, 22, 33])
    assert capture.rgb_bytes[-3:] == bytes([44, 55, 66])
    with Image.open(capture.path) as saved:
        assert saved.size == (3200, 1280)
        assert saved.getpixel((3199, 1279)) == (44, 55, 66)
    assert controller.screen_size() == (1920, 1080)


def test_legacy_fake_backend_gets_a_single_primary_layout(tmp_path):
    backend = SimpleNamespace(
        size=lambda: (800, 600),
        screenshot=lambda: Image.new("RGB", (800, 600)),
    )
    controller = DesktopController(mouse=backend, capture_dir=tmp_path)
    topology = controller.layout()
    assert topology.bounds == (0, 0, 800, 600)
    assert len(topology.monitors) == 1
    assert topology.monitors[0].primary
    assert controller.capture_screen().origin == (0, 0)


def test_capture_rejects_topology_change_without_publishing_a_file(tmp_path):
    controller, backend, _ = desktop_fixture(tmp_path)
    changed = DesktopLayout((Monitor("primary", (0, 0, 1920, 1080), True),))
    image = backend.screenshot()

    def changing_screenshot():
        backend.get_monitor_layout = lambda: changed
        return image

    backend.screenshot = changing_screenshot
    with pytest.raises(
        DesktopUnavailableError, match=r"[Tt]opology|[Mm]onitor.*changed"
    ):
        controller.capture_screen()
    assert list(tmp_path.iterdir()) == []


def test_capture_rejects_primary_only_pixels_for_virtual_desktop(tmp_path):
    controller, _, _ = desktop_fixture(tmp_path, image_size=(1920, 1080))
    with pytest.raises(DesktopUnavailableError, match=r"dimensions|size"):
        controller.capture_screen()
    assert list(tmp_path.iterdir()) == []


def test_injected_layout_failure_does_not_fall_back_to_primary(tmp_path):
    controller, backend, _ = desktop_fixture(tmp_path)

    def fail():
        raise OSError("monitor disconnected")

    backend.get_monitor_layout = fail
    with pytest.raises(DesktopUnavailableError, match=r"[Tt]opology|[Mm]onitor|layout"):
        controller.capture_screen()
    assert list(tmp_path.iterdir()) == []


def test_signed_input_coordinates_reach_backend_and_gaps_do_not(tmp_path):
    controller, _, events = desktop_fixture(tmp_path)
    controller.move(-640, -100)
    controller.click(-640, 200)
    controller.scroll(-2)
    controller.scroll(3, horizontal=True)
    controller.drag(-640, 200, -400, 300)
    assert events == [
        ("move", -640, -100),
        ("click", -640, 200),
        ("scroll", -2),
        ("hscroll", 3),
        ("move", -640, 200),
        ("down", "left"),
        ("move", -400, 300),
        ("up", "left"),
    ]
    events.clear()
    with pytest.raises(ValueError, match="outside"):
        controller.move(-1000, 1000)
    with pytest.raises(ValueError, match="outside"):
        controller.drag(-640, 200, -1000, 1000)
    assert events == []


def test_signed_drag_releases_button_when_destination_fails(tmp_path):
    controller, backend, events = desktop_fixture(tmp_path)
    move = backend.moveTo

    def fail(x, y, **kwargs):
        move(x, y, **kwargs)
        if x == -400:
            raise RuntimeError("drag interrupted")

    backend.moveTo = fail
    with pytest.raises(RuntimeError, match="interrupted"):
        controller.drag(-640, 200, -400, 300)
    assert events[-1] == ("up", "left")


@pytest.mark.parametrize("value", [-(2**31), -640, -1, 0, 65536, 2**31 - 1])
def test_coordinate_validation_accepts_signed_native_integer_range(value):
    validate_coordinate(value)


@pytest.mark.parametrize("value", [True, False, 1.5, "10", -(2**31) - 1, 2**31])
def test_coordinate_validation_rejects_nonintegers_and_native_overflow(value):
    with pytest.raises(ValueError):
        validate_coordinate(value)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows all-monitor capture")
def test_windows_capture_uses_all_screens_without_pyautogui_primary_grab(
    tmp_path, monkeypatch
):
    from PIL import ImageGrab

    topology = two_monitors()
    backend = SimpleNamespace(__name__="pyautogui", size=lambda: (1920, 1080))
    controller = DesktopController(mouse=backend, capture_dir=tmp_path)
    monkeypatch.setattr(controller, "layout", lambda: topology)
    grabs = []

    def grab(*, all_screens):
        grabs.append(all_screens)
        return Image.new("RGB", (3200, 1280), "#112233")

    monkeypatch.setattr(ImageGrab, "grab", grab)
    capture = controller.capture_screen()
    assert grabs == [True]
    assert capture.origin == (-1280, -200)
    assert capture.rgb_bytes[:3] == bytes([17, 34, 51])


@pytest.mark.skipif(sys.platform != "win32", reason="Windows monitor enumeration")
def test_native_monitor_discovery_failure_never_uses_primary_size(monkeypatch):
    def fail():
        raise OSError("EnumDisplayMonitors failed")

    monkeypatch.setattr(desktop_module, "_monitor_layout_win32", fail)
    backend = SimpleNamespace(__name__="pyautogui", size=lambda: (1920, 1080))
    with pytest.raises(DesktopUnavailableError, match=r"[Mm]onitor|layout"):
        DesktopController(mouse=backend).layout()


def test_capture_storage_failure_is_explicit_and_removes_partial_image(tmp_path):
    controller, backend, _ = desktop_fixture(tmp_path)
    image = backend.screenshot()

    def fail_save(path, **kwargs):
        from pathlib import Path

        Path(path).write_bytes(b"partial-png")
        raise OSError("disk is full")

    image.save = fail_save
    with pytest.raises(DesktopUnavailableError, match="Screen capture failed"):
        controller.capture_screen()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(sys.platform != "win32", reason="Physical Windows DPI context")
@pytest.mark.parametrize("operation", ["size", "position", "move", "click", "drag"])
def test_direct_native_actions_keep_physical_dpi_until_input_finishes(
    tmp_path, monkeypatch, operation
):
    controller, backend, events = desktop_fixture(tmp_path)
    backend.__name__ = "pyautogui"
    depth = 0

    @contextmanager
    def physical_context():
        nonlocal depth
        depth += 1
        try:
            yield
        finally:
            depth -= 1

    def physical_call(value):
        assert depth > 0, "Native coordinates escaped the physical DPI context"
        return value

    monkeypatch.setattr(desktop_module, "desktop_coordinate_context", physical_context)
    backend.size = lambda: physical_call((1920, 1080))
    backend.position = lambda: physical_call((-640, 200))
    backend.moveTo = lambda *args, **kwargs: physical_call(events.append("move"))
    backend.click = lambda *args, **kwargs: physical_call(events.append("click"))
    backend.mouseDown = lambda **kwargs: physical_call(events.append("down"))
    backend.mouseUp = lambda **kwargs: physical_call(events.append("up"))
    operations = {
        "size": controller.screen_size,
        "position": controller.pointer_position,
        "move": lambda: controller.move(-640, 200),
        "click": lambda: controller.click(-640, 200),
        "drag": lambda: controller.drag(-640, 200, -400, 300),
    }
    operations[operation]()
    assert depth == 0


@pytest.mark.skipif(sys.platform != "win32", reason="Installed Windows PyAutoGUI")
def test_installed_pyautogui_preserves_signed_coordinates_at_physical_boundary(
    tmp_path, monkeypatch
):
    import pyautogui

    position = [20, 20]
    held = set()
    events = []

    def move(x, y):
        position[:] = [x, y]
        events.append(("move", x, y))

    def release(x, y, button):
        held.discard(button)
        events.append(("up", x, y, button))

    # Replace every OS effect, retaining installed normalization, failsafe,
    # interpolation and dispatch. This never sends input to the real desktop.
    monkeypatch.setattr(
        pyautogui,
        "platformModule",
        SimpleNamespace(
            _size=lambda: (1920, 1080),
            _position=lambda: tuple(position),
            _moveTo=move,
            _click=lambda x, y, button: events.append(("click", x, y, button)),
            _mouseDown=lambda x, y, button: held.add(button),
            _mouseUp=release,
            _sendMouseEvent=lambda flag, x, y, dwData=0: events.append(  # noqa: N803
                ("wheel", flag, x, y, dwData)
            ),
        ),
    )
    monkeypatch.setattr(pyautogui, "get_monitor_layout", two_monitors, raising=False)
    monkeypatch.setattr(pyautogui, "FAILSAFE", True)
    monkeypatch.setattr(pyautogui, "PAUSE", 0)
    controller = DesktopController(mouse=pyautogui, capture_dir=tmp_path)
    controller.click(-640, 200, clicks=2)
    controller.scroll(-3)
    controller.scroll(2, horizontal=True)
    controller.drag(-640, 200, -400, 300, duration=0.1)
    assert events.count(("click", -640, 200, "left")) == 2
    assert ("wheel", 0x0800, -640, 200, -360) in events
    assert ("wheel", 0x1000, -640, 200, 240) in events
    assert position == [-400, 300]
    assert held == set()
    with pytest.raises(pyautogui.FailSafeException):
        controller.drag(-640, 200, 0, 0, duration=0.1)
    assert events[-1] == ("up", 0, 0, "left")
    assert held == set()
    assert pyautogui.FAILSAFE is True
