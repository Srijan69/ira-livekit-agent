"""Opt-in test in an owned, temporary Windows Tk window.

IRA_RUN_DESKTOP_SMOKE=1 opens an owned fixture. Click Start test, then leave the
pointer and keyboard idle. Fresh coordinates and native events verify input;
the fixture accepts a fixed test string and no personal apps are used.
"""

import asyncio
import json
import os
import subprocess
import sys
import time
import uuid

import pytest

from desktop import DesktopController
from skill_adapters import SkillToolAdapters
from skill_execution import SkillExecutionService
from skill_format import Action, Check, Skill, Step
from skill_runs import SkillRunStore
from telemetry import SessionStatusReporter

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or os.getenv("IRA_RUN_DESKTOP_SMOKE") != "1",
    reason="opt-in owned Windows desktop fixture",
)

FIXTURE = """
import ctypes, json, sys, tkinter as tk
from ctypes import wintypes
from pathlib import Path
ctypes.windll.user32.SetProcessDPIAware()
root = tk.Tk()
root.title(sys.argv[2])
root.geometry("560x500+200+180")
root.attributes("-topmost", True)
tk.Label(root, text="IRA verification fixture — temporary test window").pack(pady=10)
entry = tk.Entry(root, width=45)
entry.pack(pady=10)
def completed(event):
    if entry.get() == "IRA desktop smoke":
        root.title(sys.argv[2] + " verified")
        Path(sys.argv[1] + ".result").write_text(entry.get(), encoding="utf-8")
entry.bind("<Return>", completed)
canvas = tk.Canvas(root, width=500, height=230, bg="#17334a")
canvas.pack(pady=10)
canvas.create_text(250, 40, text="IRA mouse verification area", fill="white")
state = {"down": {}, "up": {}, "wheel_x": 0, "wheel_y": 0, "released_at": []}
def save_state():
    Path(sys.argv[1] + ".mouse").write_text(json.dumps(state), encoding="utf-8")
def button(event, kind):
    canvas.focus_set()
    key = str(event.num)
    state[kind][key] = state[kind].get(key, 0) + 1
    if kind == "up":
        state["released_at"] = [event.x_root, event.y_root]
    save_state()
canvas.bind("<ButtonPress>", lambda event: button(event, "down"))
canvas.bind("<ButtonRelease>", lambda event: button(event, "up"))
root.update()
# Observe raw wheel messages independently of PyAutoGUI. Tk's default Windows
# bindings do not consistently expose horizontal-wheel input.
user32 = ctypes.windll.user32
proc_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                              wintypes.WPARAM, wintypes.LPARAM)
set_proc = user32.SetWindowLongPtrW
set_proc.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
set_proc.restype = ctypes.c_void_p
call_proc = user32.CallWindowProcW
call_proc.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
                     wintypes.WPARAM, wintypes.LPARAM]
call_proc.restype = ctypes.c_ssize_t
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
hooks = []
def watch(handle):
    original = None
    @proc_type
    def proc(hwnd, message, wparam, lparam):
        if message in (0x020A, 0x020E):
            delta = ctypes.c_short((wparam >> 16) & 0xffff).value
            state["wheel_x" if message == 0x020E else "wheel_y"] += delta
            save_state()
            return 0
        return call_proc(original, hwnd, message, wparam, lparam)
    original = set_proc(handle, -4, ctypes.cast(proc, ctypes.c_void_p))
    if not original:
        raise RuntimeError("Could not observe owned fixture messages")
    hooks.append(proc)
for handle in {root.winfo_id(), entry.winfo_id(), canvas.winfo_id(),
               user32.GetAncestor(root.winfo_id(), 2)}:
    watch(handle)
save_state()
root.lift()
root.focus_force()
entry.focus_set()
def start_test():
    start.configure(state="disabled", text="Testing — please leave mouse and keyboard idle")
    def ready():
        entry.focus_set()
        root.update_idletasks()
        Path(sys.argv[1]).write_text(json.dumps({
            "x": entry.winfo_rootx() + entry.winfo_width() // 2,
            "y": entry.winfo_rooty() + entry.winfo_height() // 2,
            "mouse_x": canvas.winfo_rootx() + 100,
            "mouse_y": canvas.winfo_rooty() + 100,
        }), encoding="utf-8")
    root.after(750, ready)
start = tk.Button(root, text="Start test — then leave mouse and keyboard idle", command=start_test)
start.pack(pady=10)
root.update()
root.after(240000, root.destroy)
root.mainloop()
"""


async def test_owned_windows_fixture_verifies_real_input_and_mouse_operations(
    tmp_path, monkeypatch
):
    ready = tmp_path / "fixture.json"
    title = "IRA fixture " + uuid.uuid4().hex[:8]
    process = subprocess.Popen(
        [sys.executable, "-c", FIXTURE, str(ready), title],
        creationflags=subprocess.CREATE_NO_WINDOW,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    desktop = DesktopController(capture_dir=tmp_path)
    bounds = []
    require_point = desktop._require_window_point

    def observe_bounds(window, x, y):
        bounds.append((x, y, window.left, window.top, window.width, window.height))
        require_point(window, x, y)

    monkeypatch.setattr(desktop, "_require_window_point", observe_bounds)
    pointer_samples = []
    move_pointer = desktop.move

    def observe_move(x, y):
        move_pointer(x, y)
        pointer_samples.append((x, y, desktop.pointer_position()))

    monkeypatch.setattr(desktop, "move", observe_move)
    service = None
    try:
        deadline = time.monotonic() + 120
        while (
            not ready.exists()
            and time.monotonic() < deadline
            and process.poll() is None
        ):
            await asyncio.sleep(0.05)
        assert ready.exists(), "Owned desktop fixture could not start"
        point = json.loads(ready.read_text(encoding="utf-8"))
        # Windows activation is asynchronous to Tk mapping the window. Wait
        # for the actual foreground observation before sending any input.
        focus_deadline = time.monotonic() + 120
        while (
            desktop.active_window_title() != title and time.monotonic() < focus_deadline
        ):
            await asyncio.sleep(0.05)
        foreground_matches = desktop.active_window_title() == title
        assert foreground_matches, (
            "Windows did not grant foreground to the test fixture"
        )
        await asyncio.sleep(1)
        adapters = SkillToolAdapters(None, desktop, None)
        service = SkillExecutionService(
            verifier=adapters.verify,
            check_validator=adapters.validate_check,
            run_store=SkillRunStore(tmp_path / "runs.sqlite3"),
            supported_checks=("active_window", "pointer_position"),
        )
        skill = Skill(
            name="Desktop fixture smoke",
            description="Type into the owned test window.",
            state="enabled",
            allowed_tools=("desktop_click", "desktop_type", "desktop_press_key"),
            steps=(
                Step(
                    1,
                    "Focus entry",
                    action=Action(
                        "tool",
                        tool="desktop_click",
                        args=(
                            ("x", str(point["x"])),
                            ("y", str(point["y"])),
                            ("window_title", title),
                        ),
                    ),
                    check=Check("active_window", title),
                ),
                Step(
                    2,
                    "Type fixture text",
                    action=Action(
                        "tool",
                        tool="desktop_type",
                        args=(("text", "IRA desktop smoke"), ("window_title", title)),
                    ),
                    check=Check("active_window", title),
                ),
                Step(
                    3,
                    "Validate fixture text",
                    action=Action(
                        "tool",
                        tool="desktop_press_key",
                        args=(("key", "enter"), ("window_title", title)),
                    ),
                    check=Check("active_window", title + " verified", within=3),
                ),
            ),
            done_check=Check("active_window", title + " verified"),
        )
        result = await service.run(
            skill, adapters.registry(), SessionStatusReporter(sink=lambda _: None)
        )
        assert result.status == "done", result.message
        assert (
            ready.with_suffix(".json.result").read_text(encoding="utf-8")
            == "IRA desktop smoke"
        )
        # Independent native messages prove axis, notch amount and button
        # release, rather than trusting a returned input-tool success string.
        current_title = title + " verified"
        x, y = point["mouse_x"], point["mouse_y"]
        operations = [
            ("desktop_move", {"x": x, "y": y}),
            ("desktop_click", {"x": x, "y": y, "clicks": 2}),
            ("desktop_click", {"x": x, "y": y, "button": "right"}),
            ("desktop_click", {"x": x, "y": y, "button": "middle"}),
            ("desktop_scroll", {"clicks": -3}),
            ("desktop_scroll", {"clicks": 2, "horizontal": True}),
            (
                "desktop_drag",
                {"start_x": x, "start_y": y, "end_x": x + 80, "end_y": y + 30},
            ),
        ]
        mouse_skill = Skill(
            "Mouse fixture smoke",
            "Observe native mouse messages",
            state="enabled",
            allowed_tools=tuple(dict.fromkeys(tool for tool, _ in operations)),
            steps=tuple(
                Step(
                    index,
                    tool,
                    action=Action(
                        "tool",
                        tool=tool,
                        args=tuple(
                            (
                                name,
                                str(value).lower()
                                if type(value) is bool
                                else str(value),
                            )
                            for name, value in {
                                **args,
                                "window_title": current_title,
                            }.items()
                        ),
                    ),
                    check=Check("active_window", current_title),
                )
                for index, (tool, args) in enumerate(operations, 1)
            ),
            done_check=Check(
                "pointer_position", json.dumps({"x": x + 80, "y": y + 30})
            ),
        )
        # Exclude a user's click used to activate the fixture before the run.
        before_mouse = json.loads(
            ready.with_suffix(".json.mouse").read_text(encoding="utf-8")
        )
        mouse_result = await service.run(
            mouse_skill, adapters.registry(), SessionStatusReporter(sink=lambda _: None)
        )
        assert mouse_result.status == "done", (
            f"{mouse_result.message}; fixture bounds: {bounds}; pointer: {pointer_samples}"
        )
        state = json.loads(ready.with_suffix(".json.mouse").read_text(encoding="utf-8"))
        for kind in ("down", "up"):
            delta = {
                button: state[kind].get(button, 0) - before_mouse[kind].get(button, 0)
                for button in ("1", "2", "3")
            }
            assert delta == {"1": 3, "2": 1, "3": 1}, (before_mouse, state)
        assert (
            state["wheel_x"] - before_mouse["wheel_x"],
            state["wheel_y"] - before_mouse["wheel_y"],
        ) == (240, -360)
        assert state["released_at"] == [x + 80, y + 30]
        # A wrong foreground scope must refuse without changing the owned field.
        with pytest.raises(ValueError, match="foreground"):
            desktop.type_in_window("wrong target", title + " missing")
        assert (
            ready.with_suffix(".json.result").read_text(encoding="utf-8")
            == "IRA desktop smoke"
        )
    finally:
        if service:
            await service.aclose()
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=5)
