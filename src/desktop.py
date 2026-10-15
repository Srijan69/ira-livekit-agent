"""Thread-friendly screen capture and desktop input primitives."""

from __future__ import annotations

import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


class DesktopUnavailableError(RuntimeError):
    """Raised when desktop automation cannot run on this host."""


@dataclass(frozen=True)
class ScreenCapture:
    path: Path
    width: int
    height: int
    captured_at: str
    rgb_bytes: bytes | None = None


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
    ) -> None:
        self._mouse = mouse
        self._loader = loader or self._load_pyautogui
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

    def screen_size(self) -> tuple[int, int]:
        try:
            width, height = self._backend.size()
        except DesktopUnavailableError:
            raise
        except Exception as exc:
            raise DesktopUnavailableError("Could not read the screen size") from exc
        return int(width), int(height)

    def _validate_point(self, x: int, y: int) -> None:
        if not isinstance(x, int) or not isinstance(y, int):
            raise ValueError("screen coordinates must be integers")
        width, height = self.screen_size()
        if not (0 <= x < width and 0 <= y < height):
            raise ValueError(
                f"screen coordinate ({x}, {y}) is outside {width}x{height}"
            )

    def move(self, x: int, y: int) -> None:
        self._validate_point(x, y)
        self._backend.moveTo(x, y, duration=0.0)

    def click(self, x: int, y: int, *, button: str = "left") -> None:
        self._validate_point(x, y)
        if button not in {"left", "right", "middle"}:
            raise ValueError("button must be left, right, or middle")
        self._backend.click(x, y, button=button)

    def scroll(self, clicks: int) -> None:
        if not isinstance(clicks, int):
            raise ValueError("scroll amount must be an integer")
        self._backend.scroll(clicks)

    def type_text(self, text: str) -> None:
        clean = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text or "")
        self._backend.write(clean, interval=0.0)

    def press(self, key: str) -> None:
        clean = (key or "").strip().lower()
        if not clean or len(clean) > 32 or any(ord(char) < 32 for char in clean):
            raise ValueError("key must be a short printable name")
        self._backend.press(clean)

    def capture_screen(self) -> ScreenCapture:
        screenshot = self._backend.screenshot()
        width, height = getattr(screenshot, "size", self.screen_size())
        self._capture_dir.mkdir(parents=True, exist_ok=True)
        path = self._capture_dir / f"ira-screen-{uuid.uuid4().hex[:10]}.png"
        screenshot.save(os.fspath(path), format="PNG")
        rgb_bytes = None
        convert = getattr(screenshot, "convert", None)
        if convert is not None:
            try:
                rgb_bytes = convert("RGB").tobytes()
            except Exception:
                rgb_bytes = None
        return ScreenCapture(
            path=path,
            width=int(width),
            height=int(height),
            captured_at=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            rgb_bytes=rgb_bytes,
        )
