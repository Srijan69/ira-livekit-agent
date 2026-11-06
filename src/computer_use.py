"""Stateful image-coordinate input for the voice agent, separate from saved skills."""

from __future__ import annotations

import io
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, replace
from typing import Callable

from PIL import Image

from desktop import (
    DesktopController,
    ScreenCapture,
    desktop_coordinate_context,
    validate_click_count,
    validate_desktop_text,
    validate_drag_duration,
    validate_mouse_button,
    validate_scroll,
)
from desktop_layout import DesktopLayout

logger = logging.getLogger(__name__)


class DesktopChangedDuringCaptureError(ValueError):
    """The capture crossed a window transition and may be retried without input."""


@dataclass(frozen=True)
class WindowStamp:
    identity: int
    title: str
    bounds: tuple[int, int, int, int]


@dataclass(frozen=True)
class DesktopTarget:
    id: str
    identity: str
    name: str
    role: str
    bounds: tuple[int, int, int, int]


@dataclass(frozen=True)
class DesktopWindow:
    id: str
    window: WindowStamp


@dataclass(frozen=True)
class DesktopFrame:
    id: str
    capture: ScreenCapture
    width: int
    height: int
    rgba_bytes: bytes
    window: WindowStamp | None
    screen_size: tuple[int, int]
    observed_at: float
    layout: DesktopLayout
    source_bounds: tuple[int, int, int, int]
    targets: tuple[DesktopTarget, ...] = ()
    windows: tuple[DesktopWindow, ...] = ()
    cursor: tuple[int, int] | None = None


class ObservedDesktop:
    """Call under the host execution lock: observe -> one action -> observe."""

    def __init__(
        self, desktop: DesktopController, *, indicator: Callable | None = None
    ):
        self.desktop = desktop
        self.indicator = indicator or desktop.input_indicator
        self._frame: DesktopFrame | None = None
        self._captures = deque()

    def invalidate(self) -> None:
        self._frame = None

    def _stamp(self, window) -> WindowStamp | None:
        if window is None:
            return None
        return WindowStamp(
            self.desktop._window_identity(window),
            str(window.title),
            (int(window.left), int(window.top), int(window.width), int(window.height)),
        )

    def _window(self) -> WindowStamp | None:
        return self._stamp(self.desktop._active_window())

    def _visible_windows(self):
        enumerate_windows = getattr(self.desktop._backend, "getAllWindows", None)
        windows = (
            enumerate_windows()
            if enumerate_windows
            else [self.desktop._active_window()]
        )
        result = []
        for window in windows:
            try:
                if (
                    window is not None
                    and window.title.strip()
                    and getattr(window, "visible", True)
                    and not getattr(window, "isMinimized", False)
                    and window.width > 0
                    and window.height > 0
                ):
                    result.append(window)
            except Exception:
                continue  # A window may be destroyed during enumeration.
            if len(result) >= 100:
                break
        return result

    def _remember_capture(self, capture):
        self._captures.append(capture.path)
        while len(self._captures) > 3:
            self._captures.popleft().unlink(missing_ok=True)

    def close(self):
        self.invalidate()
        while self._captures:
            self._captures.popleft().unlink(missing_ok=True)

    def observe(self) -> DesktopFrame:
        self.invalidate()
        with desktop_coordinate_context():
            window = self._window()
            layout = self.desktop.layout()
            screen_size = layout.bounds[2:]
            elements = (
                self.desktop.accessible_elements(self.desktop._active_window())
                if window
                else []
            )
            capture = self.desktop.capture_screen()
            self._remember_capture(capture)
            windows = tuple(
                DesktopWindow(uuid.uuid4().hex[:12], self._stamp(w))
                for w in self._visible_windows()
            )
            if self._window() != window or self.desktop.layout() != layout:
                raise DesktopChangedDuringCaptureError(
                    "Desktop changed during capture; call capture_screen again."
                )
            if (
                (capture.width, capture.height) != screen_size
                or capture.origin != layout.bounds[:2]
                or (capture.layout is not None and capture.layout != layout)
            ):
                raise ValueError(
                    "Capture and input dimensions disagree; no mouse input is allowed."
                )
            with Image.open(capture.path) as native:
                view = native.convert("RGBA")
                view.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
            cursor = (
                self.desktop.pointer_position()
                if callable(getattr(self.desktop._backend, "position", None))
                else None
            )
            frame = DesktopFrame(
                uuid.uuid4().hex,
                capture,
                view.width,
                view.height,
                view.tobytes(),
                window,
                screen_size,
                time.monotonic(),
                layout,
                layout.bounds,
                tuple(
                    DesktopTarget(
                        uuid.uuid4().hex[:12],
                        e["identity"],
                        e["name"],
                        e["role"],
                        tuple(e["bounds"]),
                    )
                    for e in elements
                ),
                windows,
                cursor,
            )
            self._frame = frame
            logger.info(
                "desktop observed",
                extra={
                    "observation_id": frame.id,
                    "image_size": (frame.width, frame.height),
                    "desktop_size": screen_size,
                    "window_id": window.identity if window else None,
                },
            )
            return frame

    def _guard(self, frame: DesktopFrame, points=(), *, require_window=True) -> None:
        current = self._window()
        layout = self.desktop.layout()
        size = layout.bounds[2:]
        if (
            (require_window and frame.window is None)
            or current != frame.window
            or layout != frame.layout
        ):
            logger.warning(
                "Desktop observation changed: window %s/%s, bounds %s/%s, display %s/%s",
                frame.window.identity if frame.window else None,
                current.identity if current else None,
                frame.window.bounds if frame.window else None,
                current.bounds if current else None,
                frame.screen_size,
                size,
                extra={
                    "expected_window_id": frame.window.identity
                    if frame.window
                    else None,
                    "current_window_id": current.identity if current else None,
                    "expected_bounds": frame.window.bounds if frame.window else None,
                    "current_bounds": current.bounds if current else None,
                    "expected_screen_size": frame.screen_size,
                    "current_screen_size": size,
                },
            )
            raise ValueError(
                "The foreground window or display changed; call capture_screen again."
            )
        window = (
            self.desktop._require_foreground(
                frame.window.title, identity=frame.window.identity
            )
            if require_window
            else None
        )
        for x, y in points:
            self.desktop._validate_point(x, y)
            if require_window:
                self.desktop._require_window_point(window, x, y)
                self.desktop._guard_points(
                    frame.window.title, ((x, y),), frame.window.identity
                )

    @staticmethod
    def _point(frame: DesktopFrame, x: int, y: int) -> tuple[int, int]:
        if (
            type(x) is not int
            or type(y) is not int
            or not (0 <= x < frame.width and 0 <= y < frame.height)
        ):
            raise ValueError(
                f"Use image pixel coordinates inside {frame.width} by {frame.height}."
            )
        # Map pixel centers, including the final pixel, without double scaling.
        left, top, width, height = frame.source_bounds
        return (
            left + int((x + 0.5) * width / frame.width),
            top + int((y + 0.5) * height / frame.height),
        )

    def _require_frame(self, observation_id):
        frame = self._frame
        if (
            frame is None
            or frame.id != observation_id
            or time.monotonic() - frame.observed_at > 60
        ):
            raise ValueError(
                "Missing, old or consumed observation; call capture_screen and use its observation_id."
            )
        return frame

    def detail(self, observation_id, x, y, width, height):
        frame = self._require_frame(observation_id)
        self.invalidate()
        if (
            any(type(v) is not int for v in (x, y, width, height))
            or x < 0
            or y < 0
            or width <= 0
            or height <= 0
            or x + width > frame.width
            or y + height > frame.height
        ):
            raise ValueError("Detail rectangle must be inside the current image.")
        with desktop_coordinate_context():
            self._guard(frame, require_window=False)
            sx, sy, sw, sh = frame.source_bounds
            left, top = sx + x * sw // frame.width, sy + y * sh // frame.height
            right = sx + (x + width) * sw // frame.width
            bottom = sy + (y + height) * sh // frame.height
            ox, oy = frame.capture.origin
            with Image.open(frame.capture.path) as native:
                view = native.crop(
                    (left - ox, top - oy, right - ox, bottom - oy)
                ).convert("RGBA")
                view.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
            self._guard(frame, require_window=False)
            result = replace(
                frame,
                id=uuid.uuid4().hex,
                width=view.width,
                height=view.height,
                rgba_bytes=view.tobytes(),
                source_bounds=(left, top, right - left, bottom - top),
                targets=tuple(
                    replace(t, id=uuid.uuid4().hex[:12]) for t in frame.targets
                ),
                windows=tuple(
                    replace(w, id=uuid.uuid4().hex[:12]) for w in frame.windows
                ),
            )
            self._frame = result
            return result

    def focus(self, observation_id, window_id, *, _permit_check=lambda: None):
        frame = self._require_frame(observation_id)
        self.invalidate()
        with desktop_coordinate_context():
            self._guard(frame, require_window=False)
            target = next((w for w in frame.windows if w.id == window_id), None)
            if target is None:
                raise ValueError("Unknown window ID; call capture_screen.")
            matches = [
                w for w in self._visible_windows() if self._stamp(w) == target.window
            ]
            if len(matches) != 1:
                raise ValueError("The window target changed; call capture_screen.")
            _permit_check()
            self._guard(frame, require_window=False)
            matches[0].activate()
            result = self._observe_after_input()
            if (
                result.window is None
                or result.window.identity != target.window.identity
            ):
                self.invalidate()
                raise RuntimeError(
                    "Window focus was requested but not verified; capture_screen again."
                )
            return result

    @staticmethod
    def image_bounds(frame, bounds):
        sx, sy, sw, sh = frame.source_bounds
        x, y, width, height = bounds
        return [
            round((x - sx) * frame.width / sw),
            round((y - sy) * frame.height / sh),
            round(width * frame.width / sw),
            round(height * frame.height / sh),
        ]

    def describe(self, *, include_image=True, frame=None):
        frame = frame if frame is not None else self._frame
        if frame is None:
            return {}
        cursor_image_position = None
        sx, sy, sw, sh = frame.source_bounds
        if frame.cursor is not None:
            cx, cy = frame.cursor
            if sx <= cx < sx + sw and sy <= cy < sy + sh:
                cursor_image_position = [
                    (cx - sx) * frame.width // sw,
                    (cy - sy) * frame.height // sh,
                ]
        result = {
            "observation_id": frame.id,
            "coordinate_space": "image_pixels",
            "image_size": [frame.width, frame.height],
            "desktop_size": list(frame.screen_size),
            "desktop_origin": list(frame.capture.origin),
            "source_bounds": list(frame.source_bounds),
            "active_window": frame.window.title if frame.window else None,
            "cursor_image_position": cursor_image_position,
            "monitors": [
                {
                    "id": m.id,
                    "bounds": list(m.bounds),
                    "primary": m.primary,
                    "image_bounds": self.image_bounds(frame, m.bounds),
                }
                for m in frame.layout.monitors
            ],
            "targets": [
                {
                    "id": t.id,
                    "name": t.name,
                    "role": t.role,
                    "image_bounds": self.image_bounds(frame, t.bounds),
                }
                for t in frame.targets
            ],
            "windows": [
                {
                    "id": w.id,
                    "title": w.window.title,
                    "image_bounds": self.image_bounds(frame, w.window.bounds),
                }
                for w in frame.windows
            ],
        }
        if include_image:
            buffer = io.BytesIO()
            Image.frombytes(
                "RGBA", (frame.width, frame.height), frame.rgba_bytes
            ).convert("RGB").save(buffer, "JPEG", quality=85)
            result.update(image_bytes=buffer.getvalue(), image_mime_type="image/jpeg")
        return result

    def _require_target(self, target: DesktopTarget) -> None:
        current = self.desktop.accessible_elements(self.desktop._active_window())
        matches = [
            e
            for e in current
            if e["identity"] == target.identity
            and e["name"] == target.name
            and e["role"] == target.role
            and tuple(e["bounds"]) == target.bounds
        ]
        if len(matches) != 1:
            raise ValueError("The named control changed; call capture_screen again.")

    def _observe_after_input(self) -> DesktopFrame:
        for attempt in range(3):
            try:
                return self.observe()
            except DesktopChangedDuringCaptureError:
                if attempt == 2:
                    raise
                time.sleep(0.1)
        raise AssertionError("unreachable")

    def act(
        self, observation_id: str, action: str, *, _permit_check=lambda: None, **args
    ) -> DesktopFrame:
        frame = self._require_frame(observation_id)
        self.invalidate()
        with desktop_coordinate_context():
            _permit_check()
            self._guard(frame, require_window=action != "move")
            if action == "click":
                validate_mouse_button(args.get("button", "left"))
                validate_click_count(args.get("clicks", 1))
            elif action == "scroll":
                validate_scroll(args.get("clicks"), args.get("horizontal", False))
            elif action == "drag":
                validate_mouse_button(args.get("button", "left"))
                validate_drag_duration(args.get("duration", 0.5))
            elif action == "type":
                validate_desktop_text(args.get("text"))
            points = ()
            target = None
            if action == "click_target":
                target_id = args.pop("target_id", None)
                target = next((t for t in frame.targets if t.id == target_id), None)
                if target is None:
                    raise ValueError(
                        "Unknown target ID; use a target from capture_screen."
                    )
                self._require_target(target)
                left, top, width, height = target.bounds
                points = ((left + width // 2, top + height // 2),)
                action = "click"
            elif action in {"click", "move", "scroll"}:
                points = (self._point(frame, args.pop("x"), args.pop("y")),)
            elif action == "drag":
                points = (
                    self._point(frame, args.pop("start_x"), args.pop("start_y")),
                    self._point(frame, args.pop("end_x"), args.pop("end_y")),
                )
            elif action != "type":
                raise ValueError("Unsupported desktop action.")

            def guard():
                _permit_check()
                self._guard(frame, points, require_window=action != "move")

            guard()
            logger.info(
                "desktop input mapped",
                extra={
                    "observation_id": frame.id,
                    "desktop_action": action,
                    "native_points": points,
                },
            )
            with self.indicator(points, action):
                if target is not None:
                    self._require_target(target)
                guard()
                if action == "click":
                    self.desktop.click(*points[0], **args)
                elif action == "move":
                    self.desktop.move(*points[0])
                elif action == "scroll":
                    self.desktop.move(*points[0])
                    guard()
                    self.desktop.scroll(**args)
                elif action == "drag":
                    self.desktop.drag(
                        *points[0],
                        *points[1],
                        **args,
                        _guard=guard,
                    )
                else:
                    self.desktop.type_in_window(args["text"], frame.window.title)
            if (
                action in {"move", "drag"}
                and self.desktop.pointer_position() != points[-1]
            ):
                raise RuntimeError(
                    "Requested pointer destination was not observed; call capture_screen."
                )
            try:
                return self._observe_after_input()
            except Exception as exc:
                raise RuntimeError(
                    "Input was sent but the result is not verified: capture_screen is required."
                ) from exc
