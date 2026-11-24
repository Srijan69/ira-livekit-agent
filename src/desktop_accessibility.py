"""Bounded, read-only Windows accessibility snapshots in an isolated process."""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
from contextlib import contextmanager, suppress
from pathlib import Path


def read_elements(hwnd: int) -> list[dict]:
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), str(hwnd)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=5,
        check=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return json.loads(result.stdout)


class FocusObservationError(RuntimeError):
    """An isolated focused-control query failed or exceeded its bound."""


class FocusObserver:
    """Reuse an isolated UIA process for bounded, focused-control reads."""

    def __init__(self):
        self.process = None
        self.output = queue.Queue()
        self.reader = None

    def _read(self, timeout):
        try:
            line = self.output.get(timeout=timeout)
        except queue.Empty:
            raise FocusObservationError(
                "Focused-control observation timed out."
            ) from None
        if line is None:
            raise FocusObservationError("Focused-control observer ended.")
        try:
            result = json.loads(line)
        except ValueError:
            raise FocusObservationError(
                "Focused-control response is invalid."
            ) from None
        if not isinstance(result, dict) or result.get("error"):
            raise FocusObservationError("Focused-control observation failed.")
        return result

    def start(self):
        self.process = subprocess.Popen(
            [sys.executable, "-u", str(Path(__file__).resolve()), "--focused"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

        process = self.process

        def read_output():
            try:
                for line in process.stdout:
                    self.output.put(line)
            finally:
                self.output.put(None)

        self.reader = threading.Thread(target=read_output, daemon=True)
        self.reader.start()
        if self._read(5).get("ready") is not True:
            raise FocusObservationError("Focused-control observer did not initialize.")

    def snapshot(self):
        if self.process is None or self.process.poll() is not None:
            raise FocusObservationError("Focused-control observer ended.")
        try:
            self.process.stdin.write("focus\n")
            self.process.stdin.flush()
        except OSError:
            raise FocusObservationError("Focused-control observer ended.") from None
        result = self._read(1)
        control = result.get("control")
        if (
            not isinstance(control, dict)
            or not control.get("identity")
            or control.get("has_keyboard_focus") is not True
        ):
            raise FocusObservationError("Focused control is unavailable.")
        return [control]

    def close(self):
        process = self.process
        if process is None:
            return
        self.process = None
        if process.stdin is not None:
            with suppress(OSError):
                process.stdin.close()
        try:
            process.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
        if self.reader is not None:
            self.reader.join(timeout=1)
        if process.stdout is not None:
            process.stdout.close()


@contextmanager
def focused_control_observer():
    observer = FocusObserver()
    try:
        try:
            observer.start()
        except Exception as exc:
            raise FocusObservationError(
                "Focused-control observer could not initialize."
            ) from exc
        yield observer.snapshot
    finally:
        observer.close()


def _read_cached_focus(automation, cache, control_types):
    # Fetch all properties in one provider round trip, freshly for every read.
    # Cached getters then avoid separate COM/provider calls per property.
    element = automation.GetFocusedElementBuildCache(cache)
    if not element:
        raise FocusObservationError("No focused control")
    values = {
        property_id: element.GetCachedPropertyValue(property_id)
        for property_id in (30000, 30001, 30003, 30005, 30008, 30019)
    }
    return {
        "identity": ":".join(map(str, values[30000])),
        "name": str(values[30005] or "")[:240],
        "role": control_types[values[30003]].removesuffix("Control"),
        "bounds": list(values[30001]),
        "has_keyboard_focus": bool(values[30008]),
        "is_password": bool(values[30019]),
    }


def _focused_main():
    import comtypes.client
    import uiautomation as auto

    from desktop import desktop_coordinate_context

    sys.stdout.reconfigure(encoding="utf-8")
    with desktop_coordinate_context():
        core = comtypes.client.GetModule("UIAutomationCore.dll")
        automation = comtypes.client.CreateObject(
            core.CUIAutomation, interface=core.IUIAutomation
        )
        cache = automation.CreateCacheRequest()
        cache.TreeScope = 1  # TreeScope_Element: no descendants/tree traversal.
        cache.AutomationElementMode = (
            1  # Cached properties only, no live element proxy.
        )
        for property_id in (30000, 30001, 30003, 30005, 30008, 30019):
            cache.AddProperty(property_id)
        print(json.dumps({"ready": True}), flush=True)
        for command in sys.stdin:
            if command.strip() != "focus":
                continue
            try:
                result = {
                    "control": _read_cached_focus(
                        automation, cache, auto.ControlTypeNames
                    )
                }
            except Exception:
                result = {"error": "Focused control unavailable"}
            print(json.dumps(result, ensure_ascii=False), flush=True)


def _main(hwnd):
    from collections import deque

    import uiautomation as auto

    from desktop import desktop_coordinate_context

    with desktop_coordinate_context():
        root = auto.ControlFromHandle(hwnd)
        pending = deque([(root, 0)]) if root else deque()
        result = []
        visited = 0
        roles = {
            "ButtonControl",
            "HyperlinkControl",
            "ListItemControl",
            "MenuItemControl",
            "EditControl",
            "TabItemControl",
            "CheckBoxControl",
            "RadioButtonControl",
            "ComboBoxControl",
            "TreeItemControl",
            "TextControl",
        }
        while pending and visited < 600 and len(result) < 160:
            control, depth = pending.popleft()
            visited += 1
            try:
                if control.IsPassword:
                    continue
                if depth < 16:
                    pending.extend(
                        (child, depth + 1) for child in control.GetChildren()
                    )
                if (
                    control.ControlTypeName not in roles
                    or control.IsOffscreen
                    or not control.IsEnabled
                ):
                    continue
                name = control.Name.strip()
                rect = control.BoundingRectangle
                if not name or rect.width() <= 0 or rect.height() <= 0:
                    continue
                result.append(
                    {
                        "identity": ":".join(map(str, control.GetRuntimeId())),
                        "name": name[:240],
                        "role": control.ControlTypeName.removesuffix("Control"),
                        "bounds": [rect.left, rect.top, rect.width(), rect.height()],
                        "has_keyboard_focus": bool(control.HasKeyboardFocus),
                    }
                )
            except Exception:
                # Providers may destroy individual controls during enumeration.
                continue
        sys.stdout.reconfigure(encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    if sys.argv[1] == "--focused":
        _focused_main()
    else:
        _main(int(sys.argv[1]))
