"""Opt-in native check: the IRA overlay must never own focus or mouse input."""

import ctypes
import os
import sys
import time
from ctypes import wintypes

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or os.getenv("IRA_RUN_DESKTOP_SMOKE") != "1",
    reason="opt-in native input indicator",
)


@pytest.mark.parametrize("repeat", range(3))
def test_indicator_is_visible_nonactivating_clickthrough_and_cleans_up(repeat):
    from desktop_indicator import input_indicator

    api = ctypes.WinDLL("user32", use_last_error=True)
    api.GetForegroundWindow.restype = wintypes.HWND
    api.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    api.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    api.IsWindowVisible.argtypes = [wintypes.HWND]
    api.IsWindow.argtypes = [wintypes.HWND]
    api.WindowFromPoint.argtypes = [wintypes.POINT]
    api.WindowFromPoint.restype = wintypes.HWND
    point = wintypes.POINT()
    api.GetCursorPos(ctypes.byref(point))
    foreground = api.GetForegroundWindow()
    hit = api.WindowFromPoint(point)
    with (
        pytest.raises(RuntimeError, match="test input failure"),
        input_indicator((), "click") as hwnd,
    ):
        time.sleep(0.2)  # Include delayed window-manager activation after mapping.
        assert hwnd and api.IsWindowVisible(hwnd)
        style = api.GetWindowLongPtrW(hwnd, -20)
        assert style & 0x08000000  # NOACTIVATE
        assert style & 0x00080000  # LAYERED
        assert style & 0x00000020  # TRANSPARENT
        assert api.GetForegroundWindow() == foreground
        assert api.WindowFromPoint(point) == hit
        raise RuntimeError("test input failure")
    assert not api.IsWindow(hwnd)
    assert api.GetForegroundWindow() == foreground
