"""Opt-in Unicode/indicator verification in a disposable native editor only."""

import ctypes
import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from ctypes import wintypes as w

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or os.getenv("IRA_RUN_DESKTOP_SMOKE") != "1",
    reason="opt-in owned native Unicode editor",
)

_EDITOR_APP = r"""
import ctypes, json, queue, sys, threading, time
from ctypes import wintypes as w
api = ctypes.WinDLL("user32", use_last_error=True)
api.SetProcessDPIAware()
api.CreateWindowExW.argtypes = [w.DWORD,w.LPCWSTR,w.LPCWSTR,w.DWORD,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,w.HWND,w.HMENU,w.HINSTANCE,ctypes.c_void_p]
api.CreateWindowExW.restype = w.HWND
api.SetForegroundWindow.argtypes = [w.HWND]
api.SetFocus.argtypes = [w.HWND]
api.ShowWindow.argtypes = [w.HWND,ctypes.c_int]
api.GetWindowTextW.argtypes = [w.HWND,w.LPWSTR,ctypes.c_int]
api.DestroyWindow.argtypes = [w.HWND]
api.GetForegroundWindow.restype = w.HWND
api.GetWindowThreadProcessId.argtypes = [w.HWND,ctypes.POINTER(w.DWORD)]
api.GetWindowThreadProcessId.restype = w.DWORD
api.AttachThreadInput.argtypes = [w.DWORD,w.DWORD,w.BOOL]
kernel = ctypes.WinDLL("kernel32")
kernel.GetCurrentThreadId.restype = w.DWORD
def focus(edit):
    current = kernel.GetCurrentThreadId()
    foreground = api.GetWindowThreadProcessId(api.GetForegroundWindow(),None)
    attached = current != foreground and api.AttachThreadInput(current,foreground,True)
    try:
        api.SetForegroundWindow(hwnd)
        api.SetFocus(edit)
    finally:
        if attached:
            api.AttachThreadInput(current,foreground,False)
hwnd = api.CreateWindowExW(0,"STATIC",sys.argv[1],0x00CF0000,160,140,640,240,None,None,None,None)
first = api.CreateWindowExW(0x200,"EDIT","",0x50010080,30,45,560,35,hwnd,None,None,None)
second = api.CreateWindowExW(0x200,"EDIT","",0x50010080,30,100,560,35,hwnd,None,None,None)
api.ShowWindow(hwnd,5)
focus(first)
print(json.dumps({"hwnd":hwnd,"first":first,"second":second}),flush=True)
commands = queue.Queue()
def receive():
    for line in sys.stdin:
        commands.put(line.strip())
    commands.put("quit")
threading.Thread(target=receive,daemon=True).start()
message = w.MSG()
running = True
while running:
    while api.PeekMessageW(ctypes.byref(message),None,0,0,1):
        api.TranslateMessage(ctypes.byref(message))
        api.DispatchMessageW(ctypes.byref(message))
    try:
        command = commands.get(timeout=0.01)
    except queue.Empty:
        continue
    if command == "quit":
        running = False
    elif command in ("first","second"):
        focus(first if command == "first" else second)
        print(json.dumps({"focused":command}),flush=True)
    elif command == "text":
        values = []
        for edit in (first,second):
            buffer = ctypes.create_unicode_buffer(4096)
            api.GetWindowTextW(edit,buffer,4096)
            values.append(buffer.value)
        print(json.dumps({"values":values}),flush=True)
api.DestroyWindow(hwnd)
"""


@pytest.fixture
def native_editor():
    from desktop import DesktopController

    # Establish PyAutoGUI's native coordinate backend before creating the fixture.
    desktop = DesktopController()
    desktop.screen_size()
    api = ctypes.WinDLL("user32", use_last_error=True)
    api.GetForegroundWindow.restype = w.HWND
    api.SetForegroundWindow.argtypes = [w.HWND]
    previous = api.GetForegroundWindow()
    title = "IRA disposable Unicode editor " + uuid.uuid4().hex[:8]
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", _EDITOR_APP, title],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    output = queue.Queue()

    def receive():
        for line in process.stdout:
            output.put(line)

    threading.Thread(target=receive, daemon=True).start()

    def read():
        return json.loads(output.get(timeout=5))

    def command(value):
        process.stdin.write(value + "\n")
        process.stdin.flush()
        return read()

    try:
        handles = read()
        # Let activation/teardown messages from the prior disposable editor settle.
        time.sleep(0.2)
        command("first")
        assert api.GetForegroundWindow() == handles["hwnd"], (
            "Owned editor did not receive foreground focus"
        )
        yield api, handles, title, command
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)
        errors = process.stderr.read()
        process.stdout.close()
        process.stderr.close()
        if previous:
            api.SetForegroundWindow(previous)
        time.sleep(0.1)
        assert not errors, errors


def test_native_unicode_types_into_only_owned_editor(native_editor):
    from desktop import DesktopController

    _, handles, title, command = native_editor
    desktop = DesktopController()
    assert desktop.keyboard_focus() == handles["first"]
    desktop.type_in_window("IRA café नमस्ते 中文 🙂", title)
    assert command("text")["values"] == ["IRA café नमस्ते 中文 🙂", ""]
    command("second")
    assert desktop.keyboard_focus() == handles["second"]
    desktop.type_in_window("second field", title)
    assert command("text")["values"] == ["IRA café नमस्ते 中文 🙂", "second field"]


def test_native_focused_control_queries_are_fast_and_detect_field_switch(native_editor):
    from desktop_accessibility import FocusObserver

    api, handles, _, command = native_editor
    observer = FocusObserver()
    started = time.monotonic()
    try:
        observer.start()
        command("first")
        first = observer.snapshot()
        query_started = time.monotonic()
        samples = [observer.snapshot() for _ in range(10)]
        assert all(sample == first for sample in samples), {
            "expected_foreground": handles["hwnd"],
            "actual_foreground": api.GetForegroundWindow(),
            "first": [
                {key: value for key, value in item.items() if key != "name"}
                for item in first
            ],
            "samples": [
                [
                    {key: value for key, value in item.items() if key != "name"}
                    for item in sample
                ]
                for sample in samples
            ],
        }
        query_elapsed = time.monotonic() - query_started
        command("second")
        second = observer.snapshot()
        assert first[0]["identity"] != second[0]["identity"]
        # Ten focused reads must fit well below the host action's 30s bound.
        assert query_elapsed < 3
    finally:
        observer.close()
    assert observer.process is None
    assert not observer.reader.is_alive()
    print(
        json.dumps(
            {
                "focused_queries": 10,
                "query_seconds": round(query_elapsed, 3),
                "total_seconds": round(time.monotonic() - started, 3),
            }
        )
    )


def test_native_uia_guard_stops_text_when_native_child_identity_is_shared(
    native_editor, monkeypatch
):
    import desktop as desktop_module

    _, handles, title, command = native_editor
    desktop = desktop_module.DesktopController()
    # Model Chromium's shared RenderWidgetHost HWND: UIA still identifies the
    # actual focused field even when the native child identity remains constant.
    monkeypatch.setattr(
        desktop, "keyboard_focus", lambda _window=None: handles["first"]
    )
    send = desktop_module._send_unicode_text_win32
    observers = []
    from desktop_accessibility import FocusObserver

    def make_observer():
        observer = FocusObserver()
        observers.append(observer)
        return observer

    monkeypatch.setattr("desktop_accessibility.FocusObserver", make_observer)

    def switch_after_first_character(text, guard):
        for index, character in enumerate(text):
            send(character, guard)
            if index == 0:
                # SendInput queues events. Confirm the editor consumed this key
                # before the fixture deliberately transfers field focus.
                deadline = time.monotonic() + 1
                while command("text")["values"] != [character, ""]:
                    assert time.monotonic() < deadline, (
                        "First character was not consumed by the owned editor"
                    )
                    time.sleep(0.01)
                command("second")

    monkeypatch.setattr(
        desktop_module, "_send_unicode_text_win32", switch_after_first_character
    )
    with pytest.raises(desktop_module.DesktopUnavailableError, match="partial"):
        desktop.type_in_window("abc", title)
    assert command("text")["values"] == ["a", ""]
    assert len(observers) == 1
    assert observers[0].process is None
    assert not observers[0].reader.is_alive()


@pytest.mark.parametrize("action", ["click", "drag"])
def test_native_target_marker_preserves_owned_focus_and_hit_testing(
    native_editor, action, tmp_path
):
    import time

    from PIL import ImageGrab

    from desktop import DesktopController, desktop_coordinate_context
    from desktop_indicator import input_indicator

    api, handles, _, _ = native_editor
    api.GetWindowRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
    api.WindowFromPoint.argtypes = [w.POINT]
    api.WindowFromPoint.restype = w.HWND
    api.IsWindow.argtypes = [w.HWND]
    api.IsWindowVisible.argtypes = [w.HWND]
    api.GetWindowLongPtrW.argtypes = [w.HWND, ctypes.c_int]
    api.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    rect = w.RECT()
    api.GetWindowRect(handles["first"], ctypes.byref(rect))
    x, y = (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2
    hit = api.WindowFromPoint(w.POINT(x, y))
    desktop = DesktopController()
    points = ((x, y),) if action == "click" else ((x, y), (x + 100, y + 50))
    with input_indicator(points, action) as marker:
        marker_rect = w.RECT()
        api.GetWindowRect(marker, ctypes.byref(marker_rect))
        right, bottom = (x + 25, y + 25) if action == "click" else (x + 125, y + 75)
        assert (
            marker_rect.left,
            marker_rect.top,
            marker_rect.right,
            marker_rect.bottom,
        ) == (x - 24, y - 24, right, bottom)
        assert api.IsWindowVisible(marker)
        style = api.GetWindowLongPtrW(marker, -20)
        assert style & 0x08000000  # NOACTIVATE
        assert style & 0x00080000  # LAYERED
        assert style & 0x00000020  # TRANSPARENT
        assert api.GetForegroundWindow() == handles["hwnd"]
        assert desktop.keyboard_focus() == handles["first"]
        assert api.WindowFromPoint(w.POINT(x, y)) == hit
        time.sleep(0.05)
        with desktop_coordinate_context():
            screenshot = ImageGrab.grab(bbox=(x - 24, y - 24, right, bottom))
        screenshot.save(tmp_path / ("native-" + action + "-marker.png"))
        assert screenshot.getpixel((24, 24))[:3] == (120, 186, 239)
    assert not api.IsWindow(marker)
