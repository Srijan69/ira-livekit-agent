"""Desktop-handle ownership over a WinAPI boundary that cannot touch the UI."""

import ctypes
import sys
from types import SimpleNamespace

import pytest
from PIL import Image

import desktop as desktop_module
from desktop import DesktopController, DesktopUnavailableError

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="Windows desktop handles"
)


class NativeFunction:
    """Mirror ctypes' default integer result truncation until a prototype is set."""

    def __init__(self, function):
        self.function = function
        self.argtypes = None
        self.restype = ctypes.c_int

    def __call__(self, *args):
        value = self.function(*args)
        if self.restype is ctypes.c_int:
            return ctypes.c_int(value).value
        return value


@pytest.fixture
def native_desktops(monkeypatch):
    previous = 0x100000010
    state = SimpleNamespace(
        previous=previous,
        active=previous,
        opened=set(),
        events=[],
        next_handle=0x200000020,
        fail_previous=False,
        fail_open=False,
        fail_attach=False,
        fail_restore=False,
        fail_close=False,
    )

    def get_thread(thread_id):
        assert thread_id == 123
        state.events.append(("previous", state.active))
        return 0 if state.fail_previous else state.active

    def open_input(flags, inherit, access):
        assert flags == 0 and not inherit and access
        if state.fail_open:
            return 0
        handle = state.next_handle
        state.next_handle += 1
        state.opened.add(handle)
        state.events.append(("open", handle))
        return handle

    def set_thread(handle):
        state.events.append(("set", handle))
        if (handle == previous and state.fail_restore) or (
            handle != previous and state.fail_attach
        ):
            return 0
        if handle != previous and handle not in state.opened:
            return 0
        state.active = handle
        return 1

    def close_desktop(handle):
        state.events.append(("close", handle))
        if state.fail_close or handle == state.active or handle not in state.opened:
            return 0
        state.opened.remove(handle)
        return 1

    user32 = SimpleNamespace(
        GetThreadDesktop=NativeFunction(get_thread),
        OpenInputDesktop=NativeFunction(open_input),
        SetThreadDesktop=NativeFunction(set_thread),
        CloseDesktop=NativeFunction(close_desktop),
    )
    kernel32 = SimpleNamespace(GetCurrentThreadId=NativeFunction(lambda: 123))
    monkeypatch.setattr(ctypes.windll, "user32", user32)
    monkeypatch.setattr(
        ctypes,
        "WinDLL",
        lambda name, **kwargs: {"user32": user32, "kernel32": kernel32}[name],
    )
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 170)  # ERROR_BUSY
    return state


def test_repeated_attachment_restores_previous_and_closes_only_owned_handles(
    native_desktops,
):
    state = native_desktops
    for _ in range(3):
        token = desktop_module._attach_input_desktop_win32()
        assert state.active in state.opened
        assert state.active > 2**32
        desktop_module._detach_input_desktop_win32(token)
        assert state.active == state.previous
        assert state.opened == set()
    assert state.events == [
        ("previous", 0x100000010),
        ("open", 0x200000020),
        ("set", 0x200000020),
        ("set", 0x100000010),
        ("close", 0x200000020),
        ("previous", 0x100000010),
        ("open", 0x200000021),
        ("set", 0x200000021),
        ("set", 0x100000010),
        ("close", 0x200000021),
        ("previous", 0x100000010),
        ("open", 0x200000022),
        ("set", 0x200000022),
        ("set", 0x100000010),
        ("close", 0x200000022),
    ]


@pytest.mark.parametrize("operation", ["capture", "titles"])
@pytest.mark.parametrize("fail_operation", [False, True])
def test_desktop_reads_restore_and_release_after_success_or_exception(
    tmp_path, native_desktops, operation, fail_operation
):
    state = native_desktops

    def read(result):
        assert state.active in state.opened, "Reading a desktop without attachment"
        if fail_operation:
            raise RuntimeError("native observation failed")
        return result

    backend = SimpleNamespace(
        size=lambda: (8, 6),
        screenshot=lambda: read(Image.new("RGB", (8, 6))),
        getAllTitles=lambda: read(["Owned test fixture"]),
    )
    controller = DesktopController(mouse=backend, capture_dir=tmp_path)
    action = (
        controller.capture_screen
        if operation == "capture"
        else controller.window_titles
    )
    if fail_operation:
        with pytest.raises(DesktopUnavailableError):
            action()
    else:
        action()
    assert state.active == state.previous
    assert state.opened == set()
    assert state.events[-2:] == [("set", 0x100000010), ("close", 0x200000020)]


@pytest.mark.parametrize("failure", ["fail_previous", "fail_open", "fail_attach"])
@pytest.mark.parametrize("operation", ["capture", "titles"])
def test_attachment_failure_prevents_desktop_reads_and_does_not_leak(
    tmp_path, native_desktops, operation, failure
):
    state = native_desktops
    setattr(state, failure, True)
    reads = []
    backend = SimpleNamespace(
        size=lambda: (8, 6),
        screenshot=lambda: reads.append("capture") or Image.new("RGB", (8, 6)),
        getAllTitles=lambda: reads.append("titles") or ["Owned fixture"],
    )
    controller = DesktopController(mouse=backend, capture_dir=tmp_path)
    action = (
        controller.capture_screen
        if operation == "capture"
        else controller.window_titles
    )
    with pytest.raises(DesktopUnavailableError, match=r"desktop|Desktop"):
        action()
    assert reads == []
    assert state.active == state.previous
    assert state.opened == set()


def test_failed_restoration_is_explicit_and_never_closes_an_attached_handle(
    native_desktops,
):
    state = native_desktops
    token = desktop_module._attach_input_desktop_win32()
    state.fail_restore = True
    with pytest.raises(DesktopUnavailableError, match=r"restore|previous"):
        desktop_module._detach_input_desktop_win32(token)
    assert not any(event[0] == "close" for event in state.events)
    assert state.active in state.opened


def test_failed_close_is_explicit_after_restoring_original_desktop(native_desktops):
    state = native_desktops
    token = desktop_module._attach_input_desktop_win32()
    state.fail_close = True
    with pytest.raises(DesktopUnavailableError, match=r"close|release"):
        desktop_module._detach_input_desktop_win32(token)
    assert state.active == state.previous
    assert state.events[-2:] == [("set", 0x100000010), ("close", 0x200000020)]


@pytest.mark.parametrize("failure", ["fail_restore", "fail_close"])
def test_capture_cleanup_failure_leaves_no_unowned_screenshot_file(
    tmp_path, native_desktops, failure
):
    state = native_desktops
    setattr(state, failure, True)
    reads = []
    backend = SimpleNamespace(
        size=lambda: (8, 6),
        screenshot=lambda: reads.append("captured") or Image.new("RGB", (8, 6)),
    )
    controller = DesktopController(mouse=backend, capture_dir=tmp_path)
    with pytest.raises(DesktopUnavailableError, match=r"restore|close"):
        controller.capture_screen()
    assert reads == ["captured"]
    assert list(tmp_path.iterdir()) == []
