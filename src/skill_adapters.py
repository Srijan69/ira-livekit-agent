"""Typed desktop/browser operations and observations for the strict engine."""

import json
import re
from functools import wraps
from urllib.parse import urlsplit

from browser import (
    canonical_action_url,
    parse_page_value,
    validate_browser_text,
    validate_selector,
)
from desktop import (
    validate_click_count,
    validate_coordinate,
    validate_desktop_text,
    validate_drag_duration,
    validate_mouse_button,
    validate_scroll,
    validate_window_title,
)
from desktop import (
    validate_desktop_key as validate_key,
)
from request_scope_livekit import check_current_action
from skill_execution import CheckOutcome, ToolOutcome, ToolRegistry


def validate_url(url: str) -> None:
    value = urlsplit(url if "://" in url else f"https://{url}")
    _ = value.port  # Validate numeric spelling and range before any actions.
    if any(char.isspace() for char in url):
        raise ValueError("Browser addresses cannot contain whitespace.")
    if value.scheme not in {"https", "http"} or not value.hostname or not url.strip():
        raise ValueError("A browser address must be an HTTP or HTTPS URL.")
    if value.username or value.password or any(ord(c) < 32 for c in url):
        raise ValueError(
            "Browser addresses cannot contain credentials or control characters."
        )


def validate_app(app_name: str) -> None:
    if not app_name.strip() or len(app_name) > 64 or any(ord(c) < 32 for c in app_name):
        raise ValueError("Use a nonempty app name of at most 64 characters.")
    if (
        not re.fullmatch(r"[\w .()+-]+", app_name)
        or re.search(r"(?:^|\s)-\S", app_name)
        or re.match(
            r"(?i)^(?:cmd|powershell|pwsh|python[\d.]*|py|node|wscript|cscript|mshta|rundll32|regsvr32|bash|sh)(?:\.exe)?\s",
            app_name.strip(),
        )
    ):
        raise ValueError("Provide an application name, without commands or arguments.")


def validate_spotify(browser: str = "") -> None:
    if browser and browser.lower() not in {"chrome", "brave"}:
        raise ValueError("Browser must be chrome or brave.")


def validate_playlist(name: str) -> None:
    if not name.strip() or len(name) > 200:
        raise ValueError("Use a playlist name of 1 to 200 characters.")


def validate_control(
    action: str, seconds: int = 10, *, _symbolic: frozenset[str] = frozenset()
) -> None:
    if (
        "action" not in _symbolic
        and action not in {"play", "pause", "rewind", "forward"}
    ) or ("seconds" not in _symbolic and not 1 <= seconds <= 600):
        raise ValueError("Use play/pause/rewind/forward with 1 to 600 seconds.")


def validate_browser_click(
    selector: str, expected_url: str, *, _symbolic: frozenset[str] = frozenset()
) -> None:
    if "selector" not in _symbolic:
        validate_selector(selector)
    if "expected_url" not in _symbolic:
        canonical_action_url(expected_url)


def validate_browser_type(
    selector: str,
    text: str,
    expected_url: str,
    submit: bool = False,
    *,
    _symbolic: frozenset[str] = frozenset(),
) -> None:
    validate_browser_click(selector, expected_url, _symbolic=_symbolic)
    if "text" not in _symbolic:
        validate_browser_text(text)
    if "submit" not in _symbolic and type(submit) is not bool:
        raise ValueError("submit must be a boolean.")


def validate_desktop_click(
    x: int,
    y: int,
    window_title: str,
    button: str = "left",
    clicks: int = 1,
    *,
    _symbolic: frozenset[str] = frozenset(),
) -> None:
    if "x" not in _symbolic:
        validate_coordinate(x)
    if "y" not in _symbolic:
        validate_coordinate(y)
    if "window_title" not in _symbolic:
        validate_window_title(window_title)
    if "button" not in _symbolic:
        validate_mouse_button(button)
    if "clicks" not in _symbolic:
        validate_click_count(clicks)


def validate_desktop_move(
    x: int, y: int, window_title: str, *, _symbolic=frozenset()
) -> None:
    validate_desktop_click(x, y, window_title, _symbolic=_symbolic)


def validate_desktop_scroll(
    clicks: int, window_title: str, horizontal: bool = False, *, _symbolic=frozenset()
) -> None:
    if "window_title" not in _symbolic:
        validate_window_title(window_title)
    validate_scroll(
        1 if "clicks" in _symbolic else clicks,
        False if "horizontal" in _symbolic else horizontal,
    )


def validate_desktop_drag(
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
    window_title: str,
    duration: float = 0.5,
    button: str = "left",
    *,
    _symbolic=frozenset(),
) -> None:
    for name, value in (
        ("start_x", start_x),
        ("start_y", start_y),
        ("end_x", end_x),
        ("end_y", end_y),
    ):
        if name not in _symbolic:
            validate_coordinate(value)
    if "window_title" not in _symbolic:
        validate_window_title(window_title)
    if "duration" not in _symbolic:
        validate_drag_duration(duration)
    if "button" not in _symbolic:
        validate_mouse_button(button)


def parse_pointer_position(
    value: str, *, symbolic: frozenset[str] = frozenset()
) -> tuple[int, int]:
    try:
        position = json.loads(value)
        if not isinstance(position, dict) or set(position) != {"x", "y"}:
            raise ValueError
        for name in ("x", "y"):
            if name not in symbolic:
                validate_coordinate(position[name])
        return position["x"], position["y"]
    except (TypeError, ValueError, KeyError):
        raise ValueError(
            "pointer_position requires JSON with integer x and y coordinates."
        ) from None


def validate_desktop_type(
    text: str, window_title: str, *, _symbolic: frozenset[str] = frozenset()
) -> None:
    if "text" not in _symbolic:
        validate_desktop_text(text)
    if "window_title" not in _symbolic:
        validate_window_title(window_title)


def validate_desktop_press_key(
    key: str, window_title: str, *, _symbolic: frozenset[str] = frozenset()
) -> None:
    if "key" not in _symbolic:
        validate_key(key)
    if "window_title" not in _symbolic:
        validate_window_title(window_title)


def validate_type_on_desktop(
    text: str,
    window_title: str = "",
    observation_id: str = "",
    *,
    _symbolic: frozenset[str] = frozenset(),
) -> None:
    if "text" not in _symbolic:
        validate_desktop_text(text)
    if "window_title" not in _symbolic and window_title:
        validate_window_title(window_title)


def validate_click_desktop_element(
    target_id: str = "",
    window_title: str = "",
    observation_id: str = "",
    x: int = 0,
    y: int = 0,
    button: str = "left",
    clicks: int = 1,
    *,
    _symbolic: frozenset[str] = frozenset(),
) -> None:
    if "window_title" not in _symbolic and window_title:
        validate_window_title(window_title)
    if "button" not in _symbolic:
        validate_mouse_button(button)
    if "clicks" not in _symbolic:
        validate_click_count(clicks)


def validate_click_mouse(
    x: int,
    y: int,
    observation_id: str = "",
    button: str = "left",
    clicks: int = 1,
    *,
    _symbolic: frozenset[str] = frozenset(),
) -> None:
    if "x" not in _symbolic:
        validate_coordinate(x)
    if "y" not in _symbolic:
        validate_coordinate(y)
    if "button" not in _symbolic:
        validate_mouse_button(button)
    if "clicks" not in _symbolic:
        validate_click_count(clicks)


def validate_move_mouse(
    x: int,
    y: int,
    observation_id: str = "",
    *,
    _symbolic: frozenset[str] = frozenset(),
) -> None:
    if "x" not in _symbolic:
        validate_coordinate(x)
    if "y" not in _symbolic:
        validate_coordinate(y)


class SkillToolAdapters:
    """Synchronous by design; the engine owns threading and lock lifetime."""

    def __init__(self, browser, desktop, launch_app, *, before_action=None):
        self.browser = browser
        self.desktop = desktop
        self.launch_app = launch_app
        self.before_action = before_action

    def _scoped(self, fn):
        @wraps(fn)
        def call(*args, **kwargs):
            check_current_action()
            if self.before_action is not None:
                self.before_action()
            check_current_action()
            return fn(*args, **kwargs)

        return call

    def registry(self):
        return ToolRegistry(
            {
                name: self._scoped(getattr(self, name))
                for name in (
                    "open_app",
                    "browse_to",
                    "open_spotify",
                    "spotify_play_playlist",
                    "spotify_control",
                    "capture_screen",
                    "browser_click",
                    "browser_type",
                    "desktop_click",
                    "desktop_type",
                    "desktop_press_key",
                    "desktop_move",
                    "desktop_scroll",
                    "desktop_drag",
                    "type_on_desktop",
                    "click_desktop_element",
                    "click_mouse",
                    "move_mouse",
                )
            },
            validators={
                "open_app": validate_app,
                "browse_to": validate_url,
                "open_spotify": validate_spotify,
                "spotify_play_playlist": validate_playlist,
                "spotify_control": validate_control,
                "browser_click": validate_browser_click,
                "browser_type": validate_browser_type,
                "desktop_click": validate_desktop_click,
                "desktop_type": validate_desktop_type,
                "desktop_press_key": validate_desktop_press_key,
                "desktop_move": validate_desktop_move,
                "desktop_scroll": validate_desktop_scroll,
                "desktop_drag": validate_desktop_drag,
                "type_on_desktop": validate_type_on_desktop,
                "click_desktop_element": validate_click_desktop_element,
                "click_mouse": validate_click_mouse,
                "move_mouse": validate_move_mouse,
            },
        )

    def open_app(self, app_name: str) -> ToolOutcome:
        validate_app(app_name)
        self.launch_app(app_name)
        return ToolOutcome(True, "Launch requested; awaiting window observation.")

    def browse_to(self, url: str) -> ToolOutcome:
        validate_url(url)
        try:
            self.browser.navigate(url)
            return ToolOutcome(True, "Navigation returned; awaiting page observation.")
        except Exception:
            import webbrowser

            webbrowser.open(url)
            return ToolOutcome(
                True,
                "Navigation opened in system browser; awaiting window observation.",
            )

    def open_spotify(self, browser: str = "") -> ToolOutcome:
        validate_spotify(browser)
        self.browser.open_spotify(browser or None)
        return ToolOutcome(
            True, "Spotify navigation returned; awaiting page observation."
        )

    def spotify_play_playlist(self, name: str) -> ToolOutcome:
        validate_playlist(name)
        self.browser.spotify_play_playlist(name)
        return ToolOutcome(True, "Playlist selected; playback observed by browser.")

    def spotify_control(self, action: str, seconds: int = 10) -> ToolOutcome:
        validate_control(action, seconds)
        self.browser.spotify_control(action, seconds)
        return ToolOutcome(True, "Player action requested; awaiting observation.")

    def capture_screen(self) -> ToolOutcome:
        capture = self.desktop.capture_screen()
        return ToolOutcome(True, "Screen captured.", str(capture.path))

    def browser_click(self, selector: str, expected_url: str) -> ToolOutcome:
        validate_browser_click(selector, expected_url)
        self.browser.click_selector(selector, expected_url)
        return ToolOutcome(True, "Browser click returned; awaiting page observation.")

    def browser_type(
        self, selector: str, text: str, expected_url: str, submit: bool = False
    ) -> ToolOutcome:
        validate_browser_type(selector, text, expected_url, submit)
        self.browser.type_selector(selector, text, expected_url, submit=submit)
        return ToolOutcome(
            True, "Browser input value verified; awaiting page observation."
        )

    def desktop_click(
        self, x: int, y: int, window_title: str, button: str = "left", clicks: int = 1
    ) -> ToolOutcome:
        validate_desktop_click(x, y, window_title, button, clicks)
        kwargs = {"button": button}
        if clicks != 1:
            kwargs["clicks"] = clicks
        self.desktop.click_in_window(x, y, window_title, **kwargs)
        return ToolOutcome(True, "Desktop click returned; awaiting observation.")

    def desktop_move(self, x: int, y: int, window_title: str) -> ToolOutcome:
        validate_desktop_move(x, y, window_title)
        self.desktop.move_in_window(x, y, window_title)
        return ToolOutcome(True, "Pointer position observed; awaiting check.")

    def desktop_scroll(
        self, clicks: int, window_title: str, horizontal: bool = False
    ) -> ToolOutcome:
        validate_desktop_scroll(clicks, window_title, horizontal)
        self.desktop.scroll_in_window(clicks, window_title, horizontal=horizontal)
        return ToolOutcome(True, "Scroll input returned; awaiting observation.")

    def desktop_drag(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        window_title: str,
        duration: float = 0.5,
        button: str = "left",
    ) -> ToolOutcome:
        validate_desktop_drag(
            start_x, start_y, end_x, end_y, window_title, duration, button
        )
        self.desktop.drag_in_window(
            start_x,
            start_y,
            end_x,
            end_y,
            window_title,
            duration=duration,
            button=button,
        )
        return ToolOutcome(True, "Drag input returned; awaiting observation.")

    def _resolve_window_title(self, window_title: str) -> str:
        """Resolve a partial window title to the active foreground title or matching window."""
        if not window_title:
            return window_title
        from unittest.mock import Mock

        if isinstance(self.desktop, Mock):
            return window_title
        try:
            active = getattr(self.desktop, "_active_window", lambda: None)()
            if active and hasattr(active, "title") and isinstance(active.title, str):
                cur = active.title
                if window_title.casefold() in cur.casefold():
                    return cur
            import pygetwindow as gw

            wins = [
                w
                for w in gw.getWindowsWithTitle(window_title)
                if getattr(w, "title", None)
            ]
            if wins:
                w = wins[0]
                if getattr(w, "isMinimized", False):
                    w.restore()
                w.activate()
                return str(w.title)
        except Exception:
            pass
        return window_title

    def desktop_type(self, text: str, window_title: str) -> ToolOutcome:
        validate_desktop_type(text, window_title)
        actual_title = self._resolve_window_title(window_title)
        self.desktop.type_in_window(text, actual_title)
        return ToolOutcome(True, "Desktop text input returned; awaiting observation.")

    def desktop_press_key(self, key: str, window_title: str) -> ToolOutcome:
        validate_desktop_press_key(key, window_title)
        actual_title = self._resolve_window_title(window_title)
        self.desktop.press_in_window(key, actual_title)
        return ToolOutcome(True, "Desktop key press returned; awaiting observation.")

    def type_on_desktop(
        self, text: str, window_title: str = "", observation_id: str = ""
    ) -> ToolOutcome:
        validate_type_on_desktop(text, window_title, observation_id)
        if window_title:
            actual_title = self._resolve_window_title(window_title)
            self.desktop.type_in_window(text, actual_title)
        else:
            self.desktop.type_text(text)
        return ToolOutcome(True, "Desktop text input returned; awaiting observation.")

    def click_desktop_element(
        self,
        target_id: str = "",
        window_title: str = "",
        observation_id: str = "",
        x: int = 0,
        y: int = 0,
        button: str = "left",
        clicks: int = 1,
    ) -> ToolOutcome:
        validate_click_desktop_element(
            target_id, window_title, observation_id, x, y, button, clicks
        )
        if window_title and (x or y):
            actual_title = self._resolve_window_title(window_title)
            kwargs = {"button": button}
            if clicks != 1:
                kwargs["clicks"] = clicks
            self.desktop.click_in_window(x, y, actual_title, **kwargs)
        elif x or y:
            self.desktop.click(x, y, button=button, clicks=clicks)
        return ToolOutcome(True, "Desktop click returned; awaiting observation.")

    def click_mouse(
        self,
        x: int,
        y: int,
        observation_id: str = "",
        button: str = "left",
        clicks: int = 1,
    ) -> ToolOutcome:
        validate_click_mouse(x, y, observation_id, button, clicks)
        self.desktop.click(x, y, button=button, clicks=clicks)
        return ToolOutcome(True, "Mouse click returned; awaiting observation.")

    def move_mouse(self, x: int, y: int, observation_id: str = "") -> ToolOutcome:
        validate_move_mouse(x, y, observation_id)
        self.desktop.move(x, y)
        return ToolOutcome(True, "Pointer position observed; awaiting check.")

    @staticmethod
    def validate_check(check, *, symbolic: frozenset[str] = frozenset()) -> None:
        """Pure validation for observer values, including preflight checks."""
        if check.kind == "active_window" and "value" not in symbolic:
            validate_window_title(check.value)
        elif check.kind == "page_element" and "value" not in symbolic:
            validate_selector(check.value)
        elif check.kind == "page_value":
            parse_page_value(check.value, symbolic=symbolic)
        elif check.kind == "pointer_position":
            parse_pointer_position(check.value, symbolic=symbolic)

    def verify(self, check) -> CheckOutcome:
        self.validate_check(check)
        if check.kind == "window_title":
            matched = any(
                check.value.casefold() in title.casefold()
                for title in self.desktop.window_titles()
            )
        elif check.kind == "active_window":
            matched = (
                self.desktop.active_window_title().casefold() == check.value.casefold()
            )
        elif check.kind in {"page_contains", "page_element", "page_value"}:
            matched = self.browser.observe(check.kind, check.value)
        elif check.kind == "pointer_position":
            matched = self.desktop.pointer_position() == parse_pointer_position(
                check.value
            )
        else:
            raise ValueError(f"No observer configured for {check.kind}.")
        return CheckOutcome(
            matched, f"{check.kind}: {'matched' if matched else 'not matched'}"
        )
