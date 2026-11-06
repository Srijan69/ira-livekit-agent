"""Native accessibility -> observation -> real click -> independent Windows event."""

import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from computer_use import ObservedDesktop
from desktop import DesktopController

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or os.getenv("IRA_RUN_DESKTOP_SMOKE") != "1",
    reason="opt-in owned native profile fixture",
)


async def test_native_named_profile_and_scaled_image_click(tmp_path):
    ready = tmp_path / "profile.json"
    title = "IRA profile fixture " + uuid.uuid4().hex[:8]
    child = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).parent / "fixtures" / "desktop_profiles.py"),
            str(ready),
            title,
        ],
        creationflags=subprocess.CREATE_NO_WINDOW,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    desktop = DesktopController(capture_dir=tmp_path)
    computer = ObservedDesktop(desktop)
    try:
        print("Click Start test in", title, flush=True)
        deadline = time.monotonic() + 120
        while (
            not ready.exists() and time.monotonic() < deadline and child.poll() is None
        ):
            await asyncio.sleep(0.1)
        assert ready.exists(), "Owned fixture was not started"
        assert desktop.active_window_title() == title
        frame = await asyncio.to_thread(computer.observe)
        target = next(t for t in frame.targets if t.name == "kommidi")
        after = await asyncio.to_thread(
            computer.act, frame.id, "click_target", target_id=target.id
        )
        assert ready.with_suffix(".result").read_text() == "kommidi"
        assert "Selected kommidi" in after.window.title
        # The same observed target also exercises the image-to-native mapping,
        # independent of the target-ID action (which uses native UIA bounds).
        target = next(t for t in after.targets if t.name == "kommidi")
        left, top, width, height = target.bounds
        sx, sy, sw, sh = after.source_bounds
        x = int((left + width / 2 - sx) * after.width / sw)
        y = int((top + height / 2 - sy) * after.height / sh)
        # A new independent WM_COMMAND must recreate this result; the previous
        # target-ID click cannot make the coordinate-click check pass.
        ready.with_suffix(".result").unlink()
        try:
            await asyncio.to_thread(computer.act, after.id, "click", x=x, y=y)
        except ValueError:
            print(
                {
                    "expected": after.window,
                    "current": computer._window(),
                    "screen": desktop.screen_size(),
                }
            )
            raise
        assert ready.with_suffix(".result").read_text() == "kommidi"
        print(
            json.dumps(
                {
                    "image_size": [frame.width, frame.height],
                    "desktop_size": frame.screen_size,
                    "desktop_origin": frame.capture.origin,
                    "monitors": computer.describe(include_image=False)["monitors"],
                    "native_target": target.bounds,
                    "image_click": [x, y],
                    "selected": "kommidi",
                }
            )
        )
    finally:
        if child.poll() is None:
            child.terminate()
        _, errors = child.communicate(timeout=5)
        if child.returncode not in (0, 1):
            print(errors.decode(errors="replace"))
