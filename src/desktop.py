"""Thread-friendly screen capture and desktop input primitives."""

from __future__ import annotations

import logging
import math
import os
import re
import sys
import tempfile
import uuid
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable

from desktop_layout import DesktopLayout, Monitor


@contextmanager
def desktop_coordinate_context():
    """Use physical pixels for capture, window bounds and input on this thread."""
    previous = None
    if sys.platform == "win32":
        import ctypes

        api = ctypes.WinDLL("user32", use_last_error=True).SetThreadDpiAwarenessContext
        api.argtypes = [ctypes.c_void_p]
        api.restype = ctypes.c_void_p
        previous = api(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
        if not previous:
            raise DesktopUnavailableError(
                "Could not establish physical desktop coordinates."
            )
    try:
        yield
    finally:
        if previous:
            api(previous)


def _physical_desktop_method(method):
    """Keep native window reads and input in the same physical DPI space."""

    @wraps(method)
    def physical(self, *args, **kwargs):
        context = (
            desktop_coordinate_context()
            if self._is_windows_backend()
            else nullcontext()
        )
        with context:
            return method(self, *args, **kwargs)

    return physical


@dataclass(frozen=True)
class _DesktopAttachment:
    previous: int
    opened: int
    user32: Any


def _attach_input_desktop_win32() -> _DesktopAttachment | None:
    """Attach temporarily, retaining the borrowed desktop for restoration."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentThreadId.argtypes = []
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        user32.GetThreadDesktop.argtypes = [wintypes.DWORD]
        user32.GetThreadDesktop.restype = wintypes.HANDLE
        user32.OpenInputDesktop.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        user32.OpenInputDesktop.restype = wintypes.HANDLE
        user32.SetThreadDesktop.argtypes = [wintypes.HANDLE]
        user32.SetThreadDesktop.restype = wintypes.BOOL
        user32.CloseDesktop.argtypes = [wintypes.HANDLE]
        user32.CloseDesktop.restype = wintypes.BOOL
        previous = user32.GetThreadDesktop(kernel32.GetCurrentThreadId())
        if not previous:
            raise DesktopUnavailableError(
                "Could not read the thread's previous desktop."
            )
        opened = user32.OpenInputDesktop(0, False, 0x01FF)
        if not opened:
            raise DesktopUnavailableError("Could not open the interactive desktop.")
    except DesktopUnavailableError:
        raise
    except Exception as exc:
        raise DesktopUnavailableError("Windows desktop access is unavailable.") from exc

    try:
        if not user32.SetThreadDesktop(opened):
            raise ctypes.WinError(ctypes.get_last_error())
    except Exception as exc:
        # A failed SetThreadDesktop leaves the previous desktop active.
        try:
            if not user32.CloseDesktop(opened):
                raise ctypes.WinError(ctypes.get_last_error())
        except Exception as cleanup:
            raise DesktopUnavailableError(
                "Could not attach to the interactive desktop or close its opened handle."
            ) from cleanup
        raise DesktopUnavailableError(
            "Could not attach this thread to the interactive desktop."
        ) from exc
    return _DesktopAttachment(previous=previous, opened=opened, user32=user32)


def _detach_input_desktop_win32(attachment: _DesktopAttachment | None) -> None:
    """Restore before releasing; never close a borrowed or still-active handle."""
    if attachment is None:
        return
    import ctypes

    try:
        if not attachment.user32.SetThreadDesktop(attachment.previous):
            raise ctypes.WinError(ctypes.get_last_error())
    except Exception as exc:
        raise DesktopUnavailableError(
            "Could not restore the previous desktop; the opened desktop remains attached."
        ) from exc
    try:
        if not attachment.user32.CloseDesktop(attachment.opened):
            raise ctypes.WinError(ctypes.get_last_error())
    except Exception as exc:
        raise DesktopUnavailableError(
            "Could not close the opened desktop handle."
        ) from exc


class DesktopUnavailableError(RuntimeError):
    """Raised when desktop automation cannot run on this host."""


def _monitor_layout_win32() -> DesktopLayout:
    """Enumerate attached monitor rectangles; caller establishes physical DPI."""
    import ctypes
    from ctypes import wintypes

    class MonitorInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
            ("szDevice", wintypes.WCHAR * 32),
        ]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HMONITOR,
        wintypes.HDC,
        ctypes.POINTER(wintypes.RECT),
        wintypes.LPARAM,
    )
    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MonitorInfo)]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    user32.EnumDisplayMonitors.argtypes = [
        wintypes.HDC,
        ctypes.POINTER(wintypes.RECT),
        callback_type,
        wintypes.LPARAM,
    ]
    user32.EnumDisplayMonitors.restype = wintypes.BOOL
    monitors = []
    errors = []

    @callback_type
    def receive_monitor(handle, _dc, _rect, _data):
        # Exceptions cannot propagate through ctypes callbacks.
        try:
            info = MonitorInfo()
            info.cbSize = ctypes.sizeof(info)
            if not user32.GetMonitorInfoW(handle, ctypes.byref(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            rect = info.rcMonitor
            monitors.append(
                Monitor(
                    str(info.szDevice),
                    (
                        rect.left,
                        rect.top,
                        rect.right - rect.left,
                        rect.bottom - rect.top,
                    ),
                    bool(info.dwFlags & 1),
                )
            )
            return True
        except Exception as exc:
            errors.append(exc)
            return False

    succeeded = user32.EnumDisplayMonitors(None, None, receive_monitor, 0)
    if errors:
        raise DesktopUnavailableError("Could not read monitor geometry.") from errors[0]
    if not succeeded or not monitors:
        raise DesktopUnavailableError("Could not enumerate attached monitors.")
    return DesktopLayout(tuple(monitors))


_VERIFIED_KEYS = frozenset(
    {
        "enter",
        "tab",
        "esc",
        "escape",
        "backspace",
        "delete",
        "space",
        "left",
        "right",
        "up",
        "down",
        "home",
        "end",
        "pageup",
        "pagedown",
    }
)


def validate_window_title(window_title: str) -> None:
    if (
        not isinstance(window_title, str)
        or not window_title.strip()
        or len(window_title) > 512
        or any(ord(char) < 32 or ord(char) == 127 for char in window_title)
    ):
        raise ValueError("Use an exact foreground window title of 1 to 512 characters.")


def validate_coordinate(value: int) -> None:
    if type(value) is not int or not -(2**31) <= value < 2**31:
        raise ValueError("Screen coordinates must be signed 32-bit integers.")


def validate_mouse_button(button: str) -> None:
    if button not in {"left", "right", "middle"}:
        raise ValueError("button must be left, right, or middle")


def validate_click_count(clicks: int) -> None:
    if type(clicks) is not int or not 1 <= clicks <= 3:
        raise ValueError("clicks must be an integer from 1 to 3")


def validate_scroll(clicks: int, horizontal: bool = False) -> None:
    if type(clicks) is not int or not 1 <= abs(clicks) <= 100:
        raise ValueError("scroll must be 1 to 100 notches in either direction")
    if type(horizontal) is not bool:
        raise ValueError("horizontal must be a boolean")


def validate_drag_duration(duration: float) -> None:
    if (
        type(duration) not in (int, float)
        or not math.isfinite(duration)
        or not 0.1 <= duration <= 5
    ):
        raise ValueError("drag duration must be between 0.1 and 5 seconds")


def validate_desktop_text(text: str) -> None:
    if (
        not isinstance(text, str)
        or not 1 <= len(text) <= 4000
        or any(not 32 <= ord(char) <= 126 for char in text)
    ):
        raise ValueError(
            "Desktop text must contain 1 to 4000 printable ASCII characters."
        )


def validate_desktop_key(key: str) -> None:
    if not isinstance(key, str) or key not in _VERIFIED_KEYS:
        raise ValueError("Use a supported single navigation or editing key.")


def _point_is_in_window_win32(x: int, y: int, handle: int) -> bool:
    """Check actual hit ownership, including child controls, without input."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.WindowFromPoint.argtypes = [wintypes.POINT]
        user32.WindowFromPoint.restype = wintypes.HWND
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        user32.GetForegroundWindow.restype = wintypes.HWND
        target = user32.WindowFromPoint(wintypes.POINT(x, y))
        root = user32.GetAncestor(target, 2) if target else None
        return bool(root and root == handle and user32.GetForegroundWindow() == handle)
    except Exception:
        return False


@dataclass(frozen=True)
class ScreenCapture:
    path: Path
    width: int
    height: int
    captured_at: str
    rgb_bytes: bytes | None = None
    rgba_bytes: bytes | None = None
    origin: tuple[int, int] = (0, 0)
    layout: DesktopLayout | None = None


class DesktopController:
    """Owns explicit, bounded desktop actions.

    Methods are synchronous by design; callers in the realtime agent wrap
    them in ``asyncio.to_thread``.
    """

    def __init__(
        self,
        *,
        mouse: Any | None = None,
        loader: Callable[[], Any] | None = None,
        capture_dir: Path | str | None = None,
        input_guard: Callable[[], None] | None = None,
    ) -> None:
        self._mouse = mouse
        self._loader = loader or self._load_pyautogui
        self.input_guard = input_guard if input_guard is not None else lambda: None
        self._capture_dir = (
            Path(capture_dir) if capture_dir else Path(tempfile.gettempdir())
        )

    @staticmethod
    def _load_pyautogui() -> Any:
        try:
            import pyautogui
        except (ImportError, OSError) as exc:
            raise DesktopUnavailableError("PyAutoGUI is unavailable") from exc
        return pyautogui

    @property
    def _backend(self) -> Any:
        if self._mouse is None:
            self._mouse = self._loader()
        if self._mouse is None:
            raise DesktopUnavailableError("No desktop backend is available")
        return self._mouse

    @_physical_desktop_method
    def screen_size(self) -> tuple[int, int]:
        try:
            width, height = self._backend.size()
        except DesktopUnavailableError:
            raise
        except Exception as exc:
            raise DesktopUnavailableError("Could not read the screen size") from exc
        return int(width), int(height)

    def _is_windows_backend(self) -> bool:
        return (
            sys.platform == "win32"
            and getattr(self._backend, "__name__", "") == "pyautogui"
        )

    def layout(self) -> DesktopLayout:
        """Read physical topology; fake backends may supply their own layout."""
        try:
            injected = getattr(self._backend, "get_monitor_layout", None)
            if injected is not None:
                layout = injected()
                if not isinstance(layout, DesktopLayout):
                    raise ValueError("get_monitor_layout must return DesktopLayout")
                return layout
            if self._is_windows_backend():
                with desktop_coordinate_context():
                    return _monitor_layout_win32()
            width, height = self.screen_size()
            return DesktopLayout((Monitor("primary", (0, 0, width, height), True),))
        except DesktopUnavailableError:
            raise
        except Exception as exc:
            raise DesktopUnavailableError("Could not read monitor layout.") from exc

    def input_indicator(self, points, action):
        if (
            sys.platform == "win32"
            and getattr(self._backend, "__name__", "") == "pyautogui"
        ):
            from desktop_indicator import input_indicator

            return input_indicator(points, action)
        return nullcontext()

    @_physical_desktop_method
    def accessible_elements(self, window) -> list[dict]:
        injected = getattr(self._backend, "get_accessible_elements", None)
        if injected is not None:
            return injected(window)
        handle = getattr(window, "_hWnd", None)
        if sys.platform != "win32" or handle is None:
            return []
        try:
            from desktop_accessibility import read_elements

            return read_elements(int(handle))
        except Exception:
            logging.getLogger(__name__).warning(
                "Windows accessibility snapshot unavailable"
            )
            return []

    def window_titles(self) -> list[str]:
        """Observe open windows without changing focus."""
        hdesk = _attach_input_desktop_win32()
        try:
            return [str(title) for title in self._backend.getAllTitles() if title]
        except Exception as exc:
            raise DesktopUnavailableError("Window titles are unavailable") from exc
        finally:
            _detach_input_desktop_win32(hdesk)

    @_physical_desktop_method
    def _active_window(self) -> Any:
        hdesk = _attach_input_desktop_win32() if self._is_windows_backend() else None
        try:
            return self._backend.getActiveWindow()
        except Exception:
            raise DesktopUnavailableError(
                "The active foreground window is unavailable."
            ) from None
        finally:
            _detach_input_desktop_win32(hdesk)

    def active_window_title(self) -> str:
        """Read only the active window, without focusing any window."""
        window = self._active_window()
        try:
            return str(window.title) if window is not None else ""
        except Exception:
            raise DesktopUnavailableError(
                "The active foreground title is unavailable."
            ) from None

    @staticmethod
    def _window_identity(window: Any) -> int:
        handle = getattr(window, "_hWnd", None)
        return int(handle) if handle is not None else id(window)

    def _require_foreground(
        self, window_title: str, *, identity: int | None = None
    ) -> Any:
        window = self._active_window()
        if (
            window is None
            or str(window.title).casefold() != window_title.casefold()
            or (identity is not None and self._window_identity(window) != identity)
        ):
            raise ValueError(
                "The active foreground window does not match the expected window."
            )
        return window

    @staticmethod
    def _require_window_point(window: Any, x: int, y: int) -> None:
        try:
            left, top, width, height = (
                window.left,
                window.top,
                window.width,
                window.height,
            )
            inside = (
                width > 0
                and height > 0
                and left <= x < left + width
                and top <= y < top + height
            )
        except Exception:
            raise ValueError(
                "The active foreground window bounds are unavailable."
            ) from None
        if not inside:
            raise ValueError("The click point is outside the active foreground window.")

    @_physical_desktop_method
    def click_in_window(
        self,
        x: int,
        y: int,
        window_title: str,
        *,
        button: str = "left",
        clicks: int = 1,
    ) -> None:
        """Click only within the current expected foreground window."""
        validate_coordinate(x)
        validate_coordinate(y)
        validate_window_title(window_title)
        validate_mouse_button(button)
        validate_click_count(clicks)
        window = self._require_foreground(window_title)
        identity = self._window_identity(window)
        self._validate_point(x, y)
        window = self._require_foreground(window_title, identity=identity)
        self._require_window_point(window, x, y)
        handle = getattr(window, "_hWnd", None)
        if handle is not None and not _point_is_in_window_win32(x, y, int(handle)):
            raise ValueError(
                "The click point does not belong to the active foreground window."
            )
        self._require_foreground(window_title, identity=identity)
        try:
            self.click(x, y, button=button, clicks=clicks)
        except Exception:
            raise DesktopUnavailableError("The guarded desktop click failed.") from None

    @_physical_desktop_method
    def pointer_position(self) -> tuple[int, int]:
        x, y = self._backend.position()
        return int(x), int(y)

    def _guard_points(
        self, window_title: str, points: tuple[tuple[int, int], ...], identity=None
    ):
        validate_window_title(window_title)
        window = self._require_foreground(window_title, identity=identity)
        for x, y in points:
            self._validate_point(x, y)
            self._require_window_point(window, x, y)
            handle = getattr(window, "_hWnd", None)
            if handle is not None and not _point_is_in_window_win32(x, y, int(handle)):
                raise ValueError(
                    "Mouse target does not belong to the foreground window."
                )
        return self._window_identity(window)

    @_physical_desktop_method
    def move_in_window(self, x: int, y: int, window_title: str) -> None:
        identity = self._guard_points(window_title, ((x, y),))
        self._require_foreground(window_title, identity=identity)
        self.move(x, y)
        if self.pointer_position() != (x, y):
            raise DesktopUnavailableError(
                "The requested pointer position was not observed."
            )

    @_physical_desktop_method
    def scroll_in_window(
        self, clicks: int, window_title: str, *, horizontal: bool = False
    ) -> None:
        validate_scroll(clicks, horizontal)
        identity = self._guard_points(window_title, (self.pointer_position(),))
        self._require_foreground(window_title, identity=identity)
        self.scroll(clicks, horizontal=horizontal)

    @_physical_desktop_method
    def drag_in_window(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        window_title: str,
        *,
        duration: float = 0.5,
        button: str = "left",
    ) -> None:
        validate_drag_duration(duration)
        validate_mouse_button(button)
        points = ((start_x, start_y), (end_x, end_y))
        identity = self._guard_points(window_title, points)
        self.drag(
            start_x,
            start_y,
            end_x,
            end_y,
            duration=duration,
            button=button,
            _guard=lambda: self._guard_points(window_title, points, identity),
        )

    def type_in_window(self, text: str, window_title: str) -> None:
        """Type bounded printable text only while the intended window has focus."""
        validate_desktop_text(text)
        validate_window_title(window_title)
        window = self._require_foreground(window_title)
        self._require_foreground(window_title, identity=self._window_identity(window))
        try:
            self.type_text(text)
        except Exception:
            raise DesktopUnavailableError(
                "The guarded desktop text input failed."
            ) from None

    def press_in_window(self, key: str, window_title: str) -> None:
        validate_desktop_key(key)
        validate_window_title(window_title)
        window = self._require_foreground(window_title)
        self._require_foreground(window_title, identity=self._window_identity(window))
        try:
            self.press(key)
        except Exception:
            raise DesktopUnavailableError(
                "The guarded desktop key press failed."
            ) from None

    def _validate_point(self, x: int, y: int) -> None:
        validate_coordinate(x)
        validate_coordinate(y)
        if not self.layout().contains(x, y):
            raise ValueError(
                f"screen coordinate ({x}, {y}) is outside attached monitors"
            )

    @_physical_desktop_method
    def move(self, x: int, y: int) -> None:
        self._validate_point(x, y)
        self.input_guard()
        self._backend.moveTo(x, y, duration=0.0)

    @_physical_desktop_method
    def click(self, x: int, y: int, *, button: str = "left", clicks: int = 1) -> None:
        self._validate_point(x, y)
        validate_mouse_button(button)
        validate_click_count(clicks)
        self.input_guard()
        if clicks == 1:
            self._backend.click(x, y, button=button)
        else:
            self._backend.click(x, y, button=button, clicks=clicks, interval=0.1)

    @_physical_desktop_method
    def scroll(self, clicks: int, *, horizontal: bool = False) -> None:
        validate_scroll(clicks, horizontal)
        backend = self._backend
        if sys.platform == "win32" and getattr(backend, "__name__", "") == "pyautogui":
            # PyAutoGUI 0.9.54 aliases Windows hscroll to vertical scroll and
            # passes raw deltas. Windows needs 120 units per wheel notch.
            # Keep the public abort check, then use its physical event boundary.
            backend.failSafeCheck()
            x, y = backend.position()
            flag = 0x1000 if horizontal else 0x0800  # HWHEEL / WHEEL
            self.input_guard()
            backend.platformModule._sendMouseEvent(flag, x, y, dwData=120 * clicks)
        else:
            self.input_guard()
            (backend.hscroll if horizontal else backend.scroll)(clicks)

    @_physical_desktop_method
    def drag(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        *,
        duration: float = 0.5,
        button: str = "left",
        _guard=None,
    ) -> None:
        self._validate_point(start_x, start_y)
        self._validate_point(end_x, end_y)
        validate_drag_duration(duration)
        validate_mouse_button(button)
        self.input_guard()
        self._backend.moveTo(start_x, start_y, duration=0.0)
        if _guard:
            _guard()
        self.input_guard()
        try:
            self._backend.mouseDown(button=button)
            if _guard:
                _guard()
            self.input_guard()
            self._backend.moveTo(end_x, end_y, duration=duration)
        finally:
            self._release_drag_button(button)

    def _release_drag_button(self, button: str) -> None:
        try:
            self._backend.mouseUp(button=button)
        except Exception as exc:
            if isinstance(exc, getattr(self._backend, "FailSafeException", ())):
                # PyAutoGUI checks its corner abort before mouseUp, too. Only
                # release the held button through its physical backend; keep
                # the abort enabled and propagate it so no next action runs.
                x, y = self._backend.position()
                self._backend.platformModule._mouseUp(x, y, button)
            raise

    def type_text(self, text: str) -> None:
        clean = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text or "")
        self.input_guard()
        self._backend.write(clean, interval=0.0)

    def press(self, key: str) -> None:
        clean = (key or "").strip().lower()
        if not clean or len(clean) > 32 or any(ord(char) < 32 for char in clean):
            raise ValueError("key must be a short printable name")
        self.input_guard()
        self._backend.press(clean)

    def capture_screen(self) -> ScreenCapture:
        hdesk = _attach_input_desktop_win32()
        path = None
        try:
            try:
                context = (
                    desktop_coordinate_context()
                    if self._is_windows_backend()
                    else nullcontext()
                )
                with context:
                    layout = self.layout()
                    if self._is_windows_backend():
                        from PIL import ImageGrab

                        screenshot = ImageGrab.grab(all_screens=True)
                    else:
                        screenshot = self._backend.screenshot()
                    if self.layout() != layout:
                        raise DesktopUnavailableError(
                            "Monitor topology changed during capture."
                        )
                    left, top, width, height = layout.bounds
                    if getattr(screenshot, "size", None) != (width, height):
                        raise DesktopUnavailableError(
                            "Captured pixel dimensions do not match the monitor layout."
                        )
            finally:
                # Do not publish a file until native desktop ownership is released.
                _detach_input_desktop_win32(hdesk)
            self._capture_dir.mkdir(parents=True, exist_ok=True)
            path = self._capture_dir / f"ira-screen-{uuid.uuid4().hex[:10]}.png"
            screenshot.save(os.fspath(path), format="PNG")
            rgb_bytes = None
            rgba_bytes = None
            convert = getattr(screenshot, "convert", None)
            if convert is not None:
                try:
                    rgb_bytes = convert("RGB").tobytes()
                except Exception:
                    rgb_bytes = None
                try:
                    rgba_bytes = convert("RGBA").tobytes()
                except Exception:
                    rgba_bytes = None
            return ScreenCapture(
                path=path,
                width=int(width),
                height=int(height),
                captured_at=datetime.now(timezone.utc).isoformat(
                    timespec="milliseconds"
                ),
                rgb_bytes=rgb_bytes,
                rgba_bytes=rgba_bytes,
                origin=(left, top),
                layout=layout,
            )
        except Exception as exc:
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    logging.getLogger(__name__).warning(
                        "Could not remove a failed screen capture."
                    )
            raise DesktopUnavailableError(f"Screen capture failed: {exc}") from exc
