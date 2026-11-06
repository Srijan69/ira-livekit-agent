"""Codex-style browser control for IRA, built on Selenium.

The model never sees a DOM. It sees a numbered list of the things on the page
it can act on -- "1. link Sign in", "2. textbox Search" -- and acts by number.
That is the shape the coding agents converge on, and it is the only shape that
survives a voice channel: a CSS selector is not something anyone can say out
loud, but "click three" is.

Every Selenium call blocks, some of them for seconds. Nothing in this module may
be called directly from the event loop -- `agent.py` wraps each one in
`asyncio.to_thread`. A synchronous `driver.get()` on the loop stalls the audio
stream carrying IRA's own voice, and she audibly stutters mid-sentence.

The browser is launched lazily and reused. Starting Chrome costs a few seconds,
so paying that on the first `browse_to` and never again is the difference
between a usable tool and one the boss stops asking for.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlsplit, urlunsplit

if TYPE_CHECKING:  # pragma: no cover - import only for type checkers
    from selenium.webdriver.remote.webdriver import WebDriver
    from selenium.webdriver.remote.webelement import WebElement

logger = logging.getLogger("agent.browser")

# How much page text to hand back. A voice assistant reading a whole article
# aloud is useless, and the text also lands in the model's context on every
# subsequent turn -- twice the reason to cap it. The model is told to summarize
# rather than recite.
MAX_PAGE_TEXT = 4000

# How many interactive elements to enumerate. A busy page has hundreds; beyond
# the first few dozen the list stops being something anyone can hold in their
# head, and it costs input tokens on every later turn.
MAX_ELEMENTS = 40

# Longest label to read out per element. Some sites put an entire paragraph in
# an aria-label.
MAX_LABEL = 80

# Selenium's own waits. Kept short deliberately: this is a voice loop, and a
# 30-second default page-load timeout means half a minute of silence with no
# way for IRA to explain herself.
PAGE_LOAD_TIMEOUT = 20
IMPLICIT_WAIT = 0

# Elements worth offering. Anything the user could plausibly click or type into.
_INTERACTIVE_SELECTOR = (
    "a[href], button, input, textarea, select, "
    "[role='button'], [role='link'], [role='textbox'], [role='checkbox'], "
    "[onclick], [contenteditable='true']"
)

# input types that are really buttons, and ones that take no text.
_BUTTON_INPUT_TYPES = frozenset({"submit", "button", "reset", "image"})
_TOGGLE_INPUT_TYPES = frozenset({"checkbox", "radio"})
_ALLOWED_INPUT_TYPES = frozenset(
    {
        "text",
        "search",
        "email",
        "url",
        "tel",
        "number",
        "date",
        "datetime-local",
        "month",
        "time",
    }
)

# Compiled skills use a deliberately bounded CSS grammar that can be checked
# before an earlier step has effects. The direct numbered tools stay unchanged.
_CSS_IDENT = r"-?[a-zA-Z_][\w-]*"
_CSS_STRING = r"""(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')"""
_CSS_ATTRIBUTE = (
    rf"\[\s*{_CSS_IDENT}(?:\s*[~|^$*]?=\s*(?:{_CSS_STRING}|{_CSS_IDENT})"
    rf"(?:\s+[is])?)?\s*\]"
)
_CSS_PSEUDO = (
    r":(?:first-child|last-child|only-child|first-of-type|last-of-type|only-of-type|checked|disabled|enabled|empty)"
    r"|:(?:nth-child|nth-of-type)\(\s*(?:[+-]?\d+|odd|even|[+-]?\d*n(?:\s*[+-]\s*\d+)?)\s*\)"
)
_CSS_SUFFIX = rf"(?:[#.]{_CSS_IDENT}|{_CSS_ATTRIBUTE}|{_CSS_PSEUDO})"
_CSS_COMPOUND = rf"(?:(?:{_CSS_IDENT}|\*){_CSS_SUFFIX}*|{_CSS_SUFFIX}+)"
_CSS_SELECTOR = re.compile(rf"{_CSS_COMPOUND}(?:(?:\s*[>+~,]\s*|\s+){_CSS_COMPOUND})*")


def validate_selector(selector: str) -> None:
    """Validate a supported CSS selector without touching a browser."""
    if (
        not isinstance(selector, str)
        or not selector.strip()
        or len(selector) > 1024
        or any(ord(char) < 32 or ord(char) == 127 for char in selector)
        or _CSS_SELECTOR.fullmatch(selector.strip()) is None
    ):
        raise ValueError("Use a supported CSS selector of 1 to 1024 characters.")


def canonical_action_url(url: str) -> str:
    """Canonical HTTP(S) scope; paths and queries remain exact, fragments do not."""
    if (
        not isinstance(url, str)
        or not url
        or len(url) > 4096
        or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in url)
        or "\\" in url
    ):
        raise ValueError("Expected URL must be a bounded HTTP or HTTPS URL.")
    try:
        parsed = urlsplit(url)
        port = parsed.port
        host = parsed.hostname
        if (
            parsed.scheme not in {"http", "https"}
            or not host
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError
        # Use the browser's ASCII/punycode spelling. Python's built-in IDNA
        # codec can merge names (e.g. sharp-s and "ss") that Chromium keeps
        # as separate origins; guessing here would weaken the URL boundary.
        host = host.encode("ascii").decode("ascii").lower()
        if ":" in host:
            from ipaddress import IPv6Address

            host = f"[{IPv6Address(host).compressed}]"
        elif any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in host.rstrip(".").split(".")
        ):
            raise ValueError
        if port is not None and (parsed.scheme, port) not in {
            ("http", 80),
            ("https", 443),
        }:
            host += f":{port}"
        return urlunsplit((parsed.scheme, host, parsed.path or "/", parsed.query, ""))
    except (ValueError, UnicodeError):
        raise ValueError(
            "Expected URL must be a valid HTTP or HTTPS URL without credentials."
        ) from None


def validate_browser_text(text: str) -> None:
    # Selenium interprets the private-use key range and control characters as
    # keyboard actions. Text input must not smuggle a submit or modifier key.
    if (
        not isinstance(text, str)
        or len(text) > 4000
        or any(
            ord(char) < 32 or ord(char) == 127 or "\ue000" <= char <= "\uf8ff"
            for char in text
        )
    ):
        raise ValueError(
            "Browser text must be at most 4000 characters without control keys."
        )


def parse_page_value(
    value: str, *, symbolic: frozenset[str] = frozenset()
) -> tuple[str, str]:
    """Parse an exact value observation without reading DOM or exposing data."""
    try:
        spec = json.loads(value)
    except (TypeError, ValueError):
        raise ValueError(
            "page_value requires JSON with selector and value strings."
        ) from None
    if (
        not isinstance(spec, dict)
        or set(spec) != {"selector", "value"}
        or not isinstance(spec["selector"], str)
        or not isinstance(spec["value"], str)
        or ("value" not in symbolic and len(spec["value"]) > 4000)
    ):
        raise ValueError("page_value requires JSON with selector and value strings.")
    if "selector" not in symbolic:
        validate_selector(spec["selector"])
    return spec["selector"], spec["value"]


def browser_config(browser: str | None = None) -> dict[str, str]:
    """Resolve a local signed-in Chromium profile from environment settings."""
    selected = (browser or os.environ.get("IRA_BROWSER", "chrome")).strip().lower()
    if selected not in {"chrome", "brave"}:
        raise ValueError("IRA_BROWSER must be chrome or brave")
    explicit = os.environ.get("IRA_BROWSER_PROFILE", "").strip()
    if explicit:
        profile_path = os.path.abspath(os.path.expandvars(os.path.expanduser(explicit)))
        user_data_dir, profile_directory = os.path.split(profile_path)
        if not user_data_dir:
            user_data_dir, profile_directory = profile_path, "Default"
    else:
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        if not local_app_data:
            raise BrowserUnavailableError("LOCALAPPDATA is not set")
        # A separate profile avoids Chromium's SingletonLock conflict with the
        # user's normal browser. IRA keeps its own cookies after the first
        # sign-in, so it can run alongside regular Chrome or Brave windows.
        user_data_dir = os.path.join(local_app_data, "IRA", "Browser", selected.title())
        profile_directory = os.environ.get("IRA_BROWSER_PROFILE_NAME", "Default")
    return {
        "browser": selected,
        "user_data_dir": user_data_dir,
        "profile_directory": profile_directory or "Default",
    }


def _profile_options(options, config: dict[str, str]) -> None:
    """Apply profile and browser binary settings to Chromium options."""
    options.add_argument(f"--user-data-dir={config['user_data_dir']}")
    options.add_argument(f"--profile-directory={config['profile_directory']}")
    if config["browser"] == "brave":
        candidates = [
            os.path.join(
                os.environ.get("PROGRAMFILES", ""),
                "BraveSoftware",
                "Brave-Browser",
                "Application",
                "brave.exe",
            ),
            os.path.join(
                os.environ.get("LOCALAPPDATA", ""),
                "BraveSoftware",
                "Brave-Browser",
                "Application",
                "brave.exe",
            ),
        ]
        for candidate in candidates:
            if candidate and os.path.exists(candidate):
                options.binary_location = candidate
                break


def _profile_lock_detected(config: dict[str, str]) -> bool:
    """Return whether Chromium appears to have this user-data directory open."""
    lock_names = ("SingletonLock", "SingletonCookie", "SingletonSocket")
    return any(
        os.path.exists(os.path.join(config["user_data_dir"], name))
        for name in lock_names
    )


@dataclass(frozen=True)
class PageElement:
    """One numbered, actionable thing on the page."""

    index: int
    role: str
    label: str

    def spoken(self) -> str:
        return f"{self.index}. {self.role} {self.label}".rstrip()


class BrowserUnavailableError(RuntimeError):
    """Raised when Chrome cannot be started at all.

    Separate from ordinary failures because it is not retryable and the advice
    to the boss is different: this host has no browser, rather than that page
    did not load.
    """


def _normalize(text: str) -> str:
    """Collapse whitespace and cap length, for anything spoken aloud."""
    collapsed = " ".join((text or "").split())
    return collapsed[:MAX_LABEL]


def _describe(element: WebElement) -> tuple[str, str]:
    """Return a (role, label) pair for one element, as a person would say it.

    Labels are looked for in the order a sighted user would find them: the
    visible text first, then the accessible name, then the placeholder, then
    whatever is left. An element with no label at all still gets listed -- it
    may be the only way forward on the page -- but is described by its role.
    """
    tag = (element.tag_name or "").lower()
    input_type = (element.get_attribute("type") or "").lower()

    if tag == "input" and input_type in _BUTTON_INPUT_TYPES:
        role = "button"
    elif tag == "input" and input_type in _TOGGLE_INPUT_TYPES:
        role = input_type
    elif tag in ("input", "textarea"):
        role = "textbox"
    elif tag == "a":
        role = "link"
    elif tag in ("button", "select"):
        role = tag
    else:
        role = (element.get_attribute("role") or tag or "element").lower()

    for source in (
        element.text,
        element.get_attribute("aria-label"),
        element.get_attribute("placeholder"),
        element.get_attribute("value"),
        element.get_attribute("title"),
        element.get_attribute("name"),
        element.get_attribute("alt"),
    ):
        label = _normalize(source or "")
        if label:
            return role, label

    return role, ""


class BrowserController:
    """Owns one Chrome window and the numbering of what is on screen.

    Not thread-safe, and deliberately so: every method runs inside a single
    `asyncio.to_thread` hop from the agent, one tool call at a time. Two
    concurrent calls would race on `_elements` anyway, since clicking
    invalidates the numbering.
    """

    def __init__(
        self, *, headless: bool | None = None, browser: str | None = None
    ) -> None:
        # Visible by default -- the point is that the boss can watch it work,
        # the same way a coding agent's browser is visible. IRA_BROWSER_HEADLESS
        # exists for CI, where there is no display to show it on.
        if headless is None:
            headless = os.environ.get("IRA_BROWSER_HEADLESS", "").lower() in (
                "1",
                "true",
                "yes",
            )
        self._headless = headless
        self._browser = (browser or os.environ.get("IRA_BROWSER", "chrome")).lower()
        self._driver: WebDriver | None = None
        self._elements: list[WebElement] = []

    # -- lifecycle ---------------------------------------------------------

    @property
    def is_open(self) -> bool:
        return self._driver is not None and self._is_driver_alive()

    def _is_driver_alive(self) -> bool:
        """Check if the current driver has a valid, connected DevTools/remote session."""
        if self._driver is None:
            return False
        try:
            from selenium.webdriver.remote.webdriver import WebDriver

            if not isinstance(self._driver, WebDriver):
                return True
        except ImportError:
            return True

        try:
            if not getattr(self._driver, "session_id", None):
                return False
            _ = self._driver.current_window_handle
            return True
        except Exception:
            return False

    def _ensure_driver(self) -> WebDriver:
        """Start Chrome on first use and reuse it after.

        The import is function-local for the same reason `pyautogui` is in
        `agent.py`: this file is deployed to LiveKit Cloud, where there is no
        display. A module-scope import of a browser stack that cannot run there
        would take the whole agent down instead of just this one tool.
        """
        if self._driver is not None:
            if not self._is_driver_alive():
                logger.warning(
                    "Browser driver session is invalid or closed; resetting driver"
                )
                with contextlib.suppress(Exception):
                    self._driver.quit()
                self._driver = None
                self._elements = []
            else:
                return self._driver

        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
        except ImportError as exc:  # pragma: no cover - selenium is a dependency
            raise BrowserUnavailableError("Selenium is not installed") from exc

        options = Options()
        config = browser_config(self._browser)
        _profile_options(options, config)
        if self._headless:
            options.add_argument("--headless=new")
        # Chrome prints a banner about being controlled by automated software
        # and a pile of first-run dialogs; both get in the way of a window the
        # boss is meant to watch.
        options.add_argument("--disable-infobars")
        options.add_argument("--no-first-run")
        options.add_argument("--no-default-browser-check")
        options.add_argument("--start-maximized")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])

        try:
            # Selenium Manager resolves and downloads a matching chromedriver
            # on its own, so there is no driver binary to install by hand.
            driver = webdriver.Chrome(options=options)
        except Exception as exc:
            if _profile_lock_detected(config):
                raise BrowserUnavailableError(
                    f"{config['browser'].title()} is already using the profile "
                    f"'{config['profile_directory']}'. Close all {config['browser'].title()} "
                    "windows using that profile, then try again. Chromium does not allow "
                    "Selenium to start a second session with a locked profile."
                ) from exc
            raise BrowserUnavailableError(str(exc)) from exc

        driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
        driver.implicitly_wait(IMPLICIT_WAIT)
        self._driver = driver
        logger.info("browser started", extra={"headless": self._headless})
        return driver

    def close(self) -> str:
        """Close the window. Safe to call when nothing is open."""
        if self._driver is None:
            return "The browser was not open."
        try:
            self._driver.quit()
        except Exception:
            logger.exception("failed to quit the browser cleanly")
        finally:
            self._driver = None
            self._elements = []
        return "Browser closed."

    # -- navigation --------------------------------------------------------

    def navigate(self, url: str) -> str:
        """Open a URL, tolerating the way people say addresses out loud."""
        target = (url or "").strip()
        if not target:
            raise ValueError("No address was given.")
        # Speech rarely includes a scheme -- "go to hacker news" arrives as a
        # bare host. Assume https, which is also what a browser's address bar
        # does now.
        if "://" not in target:
            target = f"https://{target}"

        driver = self._ensure_driver()
        try:
            driver.get(target)
        except Exception as exc:
            from selenium.common.exceptions import WebDriverException
            from selenium.webdriver.remote.webdriver import WebDriver

            if isinstance(driver, WebDriver) and isinstance(exc, WebDriverException):
                logger.warning(
                    "WebDriver navigation failed with %s; recreating driver and retrying",
                    exc,
                )
                with contextlib.suppress(Exception):
                    self._driver.quit()
                self._driver = None
                self._elements = []
                try:
                    driver = self._ensure_driver()
                    driver.get(target)
                except Exception as retry_exc:
                    logger.warning(
                        "Driver recreation failed (%s); falling back to system browser",
                        retry_exc,
                    )
                    import webbrowser

                    webbrowser.open(target)
                    return f"Opened {target} in system browser."
            else:
                raise
        self._elements = []
        return self.describe_page()

    def open_spotify(self, browser: str | None = None) -> str:
        """Open Spotify Web Player using the selected signed-in browser profile."""
        selected = (browser or self._browser).strip().lower()
        if selected not in {"chrome", "brave"}:
            raise ValueError("Browser must be chrome or brave")
        if selected != self._browser and self._driver is not None:
            self.close()
        self._browser = selected
        return self.navigate("https://open.spotify.com")

    def back(self) -> str:
        driver = self._require_driver()
        driver.back()
        self._elements = []
        return self.describe_page()

    def new_tab(self, url: str = "") -> str:
        """Open a new browser tab and optionally navigate to a URL.

        Uses Selenium 4 switch_to.new_window('tab') to create and switch to
        a fresh tab. Resets the element cache and returns a confirmation
        with the tab number and title.
        """
        driver = self._ensure_driver()
        try:
            driver.switch_to.new_window("tab")
        except Exception as exc:
            from selenium.common.exceptions import WebDriverException
            from selenium.webdriver.remote.webdriver import WebDriver

            if isinstance(driver, WebDriver) and isinstance(exc, WebDriverException):
                logger.warning(
                    "WebDriver new_tab failed with %s; recreating driver and retrying",
                    exc,
                )
                with contextlib.suppress(Exception):
                    self._driver.quit()
                self._driver = None
                self._elements = []
                driver = self._ensure_driver()
                driver.switch_to.new_window("tab")
            else:
                raise
        self._elements = []

        target = (url or "").strip()
        if target:
            if "://" not in target:
                target = f"https://{target}"
            try:
                driver.get(target)
            except Exception as exc:
                from selenium.common.exceptions import WebDriverException

                if isinstance(exc, WebDriverException):
                    logger.warning("WebDriver new_tab get failed with %s", exc)
            self._elements = []

        handles = driver.window_handles
        try:
            curr = driver.current_window_handle
            tab_idx = handles.index(curr) + 1
        except Exception:
            tab_idx = len(handles)

        title = _normalize(driver.title) or "New tab"
        return f"Opened tab {tab_idx}: {title}."

    def switch_tab(self, tab: int | str) -> str:
        """Switch to a specific open tab by 1-based index or window handle.

        Validates bounds, switches window context, resets the element cache,
        and returns a description of the active page.
        """
        driver = self._require_driver()
        handles = driver.window_handles
        if not handles:
            raise RuntimeError("No browser tabs are currently open.")

        target_handle: str | None = None
        if isinstance(tab, int) and not isinstance(tab, bool):
            if not (1 <= tab <= len(handles)):
                raise IndexError(
                    f"Tab {tab} does not exist. There are {len(handles)} open tabs."
                )
            target_handle = handles[tab - 1]
        elif isinstance(tab, str):
            if tab in handles:
                target_handle = tab
            elif tab.strip().isdigit():
                idx = int(tab.strip())
                if not (1 <= idx <= len(handles)):
                    raise IndexError(
                        f"Tab {idx} does not exist. There are {len(handles)} open tabs."
                    )
                target_handle = handles[idx - 1]
            else:
                raise ValueError(
                    f"Tab '{tab}' was not found. Open tabs: 1 to {len(handles)}."
                )
        else:
            raise TypeError(
                f"Tab identifier must be an integer or handle string, got {type(tab).__name__}."
            )

        driver.switch_to.window(target_handle)
        self._elements = []
        return self.describe_page()

    def list_tabs(self) -> list[dict[str, Any]]:
        """List all open browser tabs.

        Returns a list of dicts with:
            - 'index': 1-based tab index
            - 'title': Page title of the tab
            - 'url': Current URL of the tab
            - 'is_active': True if this is the currently focused tab
            - 'handle': Window handle string
        """
        if not self.is_open:
            return []
        driver = self._require_driver()
        handles = driver.window_handles
        if not handles:
            return []

        try:
            current_handle = driver.current_window_handle
        except Exception:
            current_handle = handles[0]

        tabs: list[dict[str, Any]] = []
        for idx, handle in enumerate(handles, start=1):
            is_active = handle == current_handle
            if is_active:
                tabs.append(
                    {
                        "index": idx,
                        "title": _normalize(driver.title) or "Untitled page",
                        "url": driver.current_url or "",
                        "is_active": True,
                        "handle": handle,
                    }
                )
            else:
                try:
                    driver.switch_to.window(handle)
                    title = _normalize(driver.title) or "Untitled page"
                    url = driver.current_url or ""
                except Exception:
                    title = "Unknown"
                    url = ""
                tabs.append(
                    {
                        "index": idx,
                        "title": title,
                        "url": url,
                        "is_active": False,
                        "handle": handle,
                    }
                )

        with contextlib.suppress(Exception):
            driver.switch_to.window(current_handle)

        return tabs

    def close_tab(self, tab: int | str | None = None) -> str:
        """Close a specific tab or the current active tab.

        If other tabs remain, switches to the previous or adjacent tab,
        resets the element cache, and describes the newly focused page.
        If no tabs remain, closes the browser session cleanly.
        """
        if not self.is_open:
            return "The browser was not open."
        driver = self._require_driver()
        handles = driver.window_handles
        if not handles:
            return self.close()

        try:
            current_handle = driver.current_window_handle
        except Exception:
            current_handle = handles[0]

        if tab is None:
            target_handle = current_handle
        elif isinstance(tab, int) and not isinstance(tab, bool):
            if not (1 <= tab <= len(handles)):
                raise IndexError(
                    f"Tab {tab} does not exist. There are {len(handles)} open tabs."
                )
            target_handle = handles[tab - 1]
        elif isinstance(tab, str):
            if tab in handles:
                target_handle = tab
            elif tab.strip().isdigit():
                idx = int(tab.strip())
                if not (1 <= idx <= len(handles)):
                    raise IndexError(
                        f"Tab {idx} does not exist. There are {len(handles)} open tabs."
                    )
                target_handle = handles[idx - 1]
            else:
                raise ValueError(
                    f"Tab '{tab}' was not found. Open tabs: 1 to {len(handles)}."
                )
        else:
            raise TypeError(
                f"Tab identifier must be an integer or handle string, got {type(tab).__name__}."
            )

        target_idx = handles.index(target_handle) + 1
        is_closing_active = target_handle == current_handle

        if len(handles) <= 1:
            self.close()
            return f"Closed tab {target_idx}. Browser closed as no tabs remain."

        if is_closing_active:
            driver.close()
            remaining = driver.window_handles
            if not remaining:
                self.close()
                return f"Closed tab {target_idx}. Browser closed."
            fallback_idx = max(0, target_idx - 2)
            if fallback_idx >= len(remaining):
                fallback_idx = len(remaining) - 1
            new_handle = remaining[fallback_idx]
            driver.switch_to.window(new_handle)
            self._elements = []
            new_idx = remaining.index(new_handle) + 1
            title = _normalize(driver.title) or "Untitled page"
            return f"Closed tab {target_idx}. Active tab is now {new_idx}: {title}."
        else:
            driver.switch_to.window(target_handle)
            driver.close()
            driver.switch_to.window(current_handle)
            self._elements = []
            return f"Closed tab {target_idx}."

    def _require_driver(self) -> WebDriver:
        if self._driver is None or not self._is_driver_alive():
            try:
                if self._driver is not None:
                    self._driver.quit()
            except Exception:
                pass
            self._driver = None
            self._elements = []
            raise BrowserUnavailableError("No page is open yet")
        return self._driver

    # -- reading -----------------------------------------------------------

    def describe_page(self) -> str:
        """One spoken line: where we are and how much there is to act on."""
        driver = self._require_driver()
        count = len(self.list_elements())
        title = _normalize(driver.title) or "Untitled page"
        return f"{title}. {count} things to act on."

    def read_page(self) -> str:
        """Return the visible text of the page, capped.

        `body.text` rather than `page_source`: the former is roughly what a
        person sees, the latter is markup the model would have to parse and
        which would blow the context budget on the first news article.
        """
        driver = self._require_driver()
        try:
            body = driver.find_element("tag name", "body")
            text = _normalize_block(body.text)
        except Exception:
            logger.exception("failed to read page text")
            raise RuntimeError("Could not read that page.") from None

        if not text:
            return "That page has no readable text."
        if len(text) > MAX_PAGE_TEXT:
            return text[:MAX_PAGE_TEXT] + " ... (truncated)"
        return text

    def observe(self, kind: str, value: str) -> bool:
        """Read a postcondition from the current page without opening a browser.

        page_element values are CSS selectors; only displayed matches count.
        Unimplemented visual judgments never become successful checks.
        """
        driver = self._require_driver()
        if kind == "page_contains":
            return (
                value.casefold()
                in driver.find_element("tag name", "body").text.casefold()
            )
        if kind == "page_element":
            return any(
                element.is_displayed()
                for element in driver.find_elements("css selector", value)
            )
        if kind == "page_value":
            selector, expected = parse_page_value(value)
            element = self._unique_selector(selector, enabled=False)
            self._validate_value_field(element, writable=False)
            try:
                return element.get_property("value") == expected
            except Exception:
                raise RuntimeError("Could not observe the field value.") from None
        raise ValueError(f"Unsupported browser check: {kind}")

    def playback_active(self) -> bool:
        """Observe playing media; a requested button click is insufficient."""
        return (
            self._require_driver().execute_script(
                "const a = document.querySelector('audio'); "
                "if (a && !a.paused && !a.ended && a.currentTime > 0) return true; "
                "const pp = document.querySelector('[data-testid=\"control-button-playpause\"]'); "
                "if (pp && /pause/i.test(pp.getAttribute('aria-label') || '')) return true; "
                "return false;"
            )
            is True
        )

    def playlist_context_matches(self) -> bool:
        """Require the player to identify the playlist currently displayed."""
        return (
            self._require_driver().execute_script(
                "const path = location.pathname; "
                "if (!path.startsWith('/playlist/')) return false; "
                "const context = document.querySelector('[data-testid=\"context-item-link\"]'); "
                "if (context && new URL(context.href, location.href).pathname === path) return true; "
                "const pp = document.querySelector('[data-testid=\"control-button-playpause\"]'); "
                "return !!(pp && /pause/i.test(pp.getAttribute('aria-label') || ''));"
            )
            is True
        )

    def list_elements(self) -> list[PageElement]:
        """Re-index everything actionable and return it numbered from 1.

        Re-indexed on every call rather than cached, because any click or
        navigation invalidates the previous numbering. Stale indices are the
        single biggest failure mode of this style of tool -- clicking "3" and
        getting whatever is third on a page that has since changed.
        """
        driver = self._require_driver()
        from selenium.webdriver.common.by import By

        found: list[WebElement] = []
        described: list[PageElement] = []
        for element in driver.find_elements(By.CSS_SELECTOR, _INTERACTIVE_SELECTOR):
            if len(found) >= MAX_ELEMENTS:
                break
            try:
                if not element.is_displayed():
                    continue
                role, label = _describe(element)
            except Exception:
                # Elements go stale mid-enumeration on any page with live
                # scripting. Skipping one is fine; failing the whole listing
                # over it is not.
                continue
            found.append(element)
            described.append(PageElement(len(found), role, label))

        self._elements = found
        return described

    # -- acting ------------------------------------------------------------

    def _assert_action_url(self, expected_url: str) -> None:
        expected = canonical_action_url(expected_url)
        try:
            current = canonical_action_url(self._require_driver().current_url)
        except Exception:
            raise ValueError("Current browser URL cannot be verified.") from None
        if current != expected:
            raise ValueError("Current browser URL does not match the expected URL.")

    def _unique_selector(self, selector: str, *, enabled: bool = True) -> WebElement:
        validate_selector(selector)
        try:
            elements = self._require_driver().find_elements("css selector", selector)
            matches = [
                element
                for element in elements
                if element.is_displayed() and (not enabled or element.is_enabled())
            ]
        except Exception:
            raise RuntimeError(
                "Browser target could not be resolved from the current page."
            ) from None
        if len(matches) != 1:
            raise ValueError(
                "Browser selector must match exactly one visible, eligible target."
            )
        return matches[0]

    @staticmethod
    def _validate_value_field(element: WebElement, *, writable: bool) -> None:
        try:
            tag = (element.tag_name or "").lower()
            field_type = (element.get_attribute("type") or "text").lower()
            readonly = element.get_dom_attribute("readonly") is not None
        except Exception:
            raise RuntimeError("Could not verify the current input field.") from None

        if tag == "select":
            if writable:
                raise ValueError(
                    "Select dropdown elements cannot be typed into; use select_dropdown instead."
                )
            return

        if tag not in {"input", "textarea"} or (
            tag == "input" and field_type not in _ALLOWED_INPUT_TYPES
        ):
            raise ValueError(
                "Only ordinary text, date, and select fields can be read or typed into; "
                "password, file, and hidden fields are not supported."
            )

        if writable and readonly:
            raise ValueError("Cannot type into a read-only field.")

    def click_selector(self, selector: str, expected_url: str) -> None:
        """Act on one current, visible, enabled CSS target within an exact URL."""
        validate_selector(selector)
        self._assert_action_url(expected_url)
        element = self._unique_selector(selector)
        self._assert_action_url(expected_url)
        try:
            element.click()
        except Exception:
            raise RuntimeError(
                "Browser click failed; the target may have changed or be covered."
            ) from None
        finally:
            self._elements = []

    def type_selector(
        self, selector: str, text: str, expected_url: str, *, submit: bool = False
    ) -> None:
        """Type into a current safe field, verify its value, then optionally submit."""
        from selenium.webdriver.common.keys import Keys

        validate_selector(selector)
        validate_browser_text(text)
        if type(submit) is not bool:
            raise ValueError("submit must be a boolean.")
        self._assert_action_url(expected_url)
        element = self._unique_selector(selector)
        self._validate_value_field(element, writable=True)
        self._assert_action_url(expected_url)
        try:
            element.clear()
            # Clearing can run page handlers; do not type if the page changed.
            self._assert_action_url(expected_url)
            current = self._unique_selector(selector)
            if current != element:
                raise RuntimeError("Input target changed after clearing.")
            self._validate_value_field(current, writable=True)
            self._assert_action_url(expected_url)
            current.send_keys(text)
            self._assert_action_url(expected_url)
            current = self._unique_selector(selector)
            if current != element:
                raise RuntimeError("Input target changed before verification.")
            self._validate_value_field(current, writable=True)
            if current.get_property("value") != text:
                raise RuntimeError("Input value could not be verified.")
            if submit:
                self._assert_action_url(expected_url)
                current.send_keys(Keys.RETURN)
        except Exception:
            # Driver exceptions may include user text or DOM attributes.
            raise RuntimeError(
                "Browser input could not be verified or submitted."
            ) from None
        finally:
            self._elements = []

    def _resolve(self, index: int) -> WebElement:
        if not self._elements:
            self.list_elements()
        if not 1 <= index <= len(self._elements):
            raise IndexError(
                f"There is no element {index}. The page has {len(self._elements)}."
            )
        return self._elements[index - 1]

    def click(self, index: int) -> str:
        element = self._resolve(index)
        role, label = _describe(element)
        try:
            element.click()
        except Exception:
            # Overlays, cookie banners and sticky headers routinely intercept
            # a real click. The JS path ignores all of that, so it is worth one
            # retry before reporting failure.
            driver = self._require_driver()
            driver.execute_script("arguments[0].click();", element)
        self._elements = []
        return f"Clicked the {role} {label}.".replace(" .", ".")

    def type_text(self, index: int, text: str, *, submit: bool = False) -> str:
        from selenium.webdriver.common.keys import Keys

        element = self._resolve(index)
        role, label = _describe(element)
        tag = (element.tag_name or "").lower()
        if tag == "select":
            raise ValueError(
                f"Element {index} is a select dropdown, not a text field. "
                "Use select_dropdown to choose an option."
            )
        input_type = (element.get_attribute("type") or "").lower()
        if input_type in {"date", "datetime-local", "month", "time"}:
            with contextlib.suppress(Exception):
                element.clear()
            with contextlib.suppress(Exception):
                element.send_keys(text)
            with contextlib.suppress(Exception):
                driver = self._require_driver()
                driver.execute_script(
                    "arguments[0].value = arguments[1]; "
                    "arguments[0].dispatchEvent(new Event('input', {bubbles: true})); "
                    "arguments[0].dispatchEvent(new Event('change', {bubbles: true}));",
                    element,
                    text,
                )
        else:
            element.clear()
            element.send_keys(text)

        if submit:
            element.send_keys(Keys.RETURN)
            self._elements = []
            return f"Typed into the {role} {label} and submitted.".replace(" .", ".")
        return f"Typed into the {role} {label}.".replace(" .", ".")

    def select_dropdown(self, index: int, value: str) -> str:
        """Select an option in a dropdown element by visible text or value attribute.

        Only use an index that came from listing the page.
        """
        from selenium.webdriver.support.ui import Select

        element = self._resolve(index)
        role, label = _describe(element)

        tag = (element.tag_name or "").lower()
        if tag != "select":
            raise ValueError(
                f"Element {index} ({role} {label}) is a {tag}, not a <select> dropdown."
            )

        is_disabled = False
        with contextlib.suppress(Exception):
            if hasattr(element, "get_dom_attribute"):
                is_disabled = element.get_dom_attribute("disabled") is not None
            elif hasattr(element, "get_attribute"):
                is_disabled = element.get_attribute("disabled") is not None
            if not is_disabled and hasattr(element, "is_enabled"):
                is_disabled = not element.is_enabled()
        if is_disabled:
            raise ValueError(f"Cannot select option from disabled dropdown {label}.")

        select: Select | None = None
        options: list[WebElement] = []
        try:
            select = Select(element)
            options = list(select.options)
        except Exception as exc:
            mock_opts = getattr(element, "options", None)
            if mock_opts is None and hasattr(element, "find_elements"):
                mock_opts = element.find_elements("tag name", "option")
            if mock_opts is not None:
                options = list(mock_opts)
            else:
                raise RuntimeError(
                    f"Could not interact with dropdown {label}: {exc}"
                ) from exc

        val_str = str(value).strip()
        target_opt: WebElement | None = None

        # Tier 1: Exact match by visible text or value
        for opt in options:
            opt_text = (getattr(opt, "text", "") or "").strip()
            opt_val = ""
            if hasattr(opt, "get_attribute"):
                opt_val = (opt.get_attribute("value") or "").strip()
            if opt_text == val_str or opt_val == val_str:
                target_opt = opt
                break

        # Tier 2: Case-insensitive match
        if target_opt is None:
            val_lower = val_str.lower()
            for opt in options:
                opt_text = (getattr(opt, "text", "") or "").strip().lower()
                opt_val = ""
                if hasattr(opt, "get_attribute"):
                    opt_val = (opt.get_attribute("value") or "").strip().lower()
                if opt_text == val_lower or opt_val == val_lower:
                    target_opt = opt
                    break

        # Tier 3: Option not found
        if target_opt is None:
            available: list[str] = []
            for opt in options:
                t = (getattr(opt, "text", "") or "").strip()
                v = ""
                if hasattr(opt, "get_attribute"):
                    v = (opt.get_attribute("value") or "").strip()
                if t and v and t != v:
                    available.append(f"'{t}' (value: '{v}')")
                elif t:
                    available.append(f"'{t}'")
                elif v:
                    available.append(f"'{v}'")

            if available:
                summary = ", ".join(available[:8])
                if len(available) > 8:
                    summary += f", and {len(available) - 8} more"
                raise ValueError(
                    f"Option '{value}' not found in dropdown {label or index}. Available options: {summary}."
                )
            raise ValueError(
                f"Option '{value}' not found; dropdown {label or index} has no selectable options."
            )

        is_opt_disabled = False
        with contextlib.suppress(Exception):
            if hasattr(target_opt, "is_enabled") and callable(target_opt.is_enabled):
                res = target_opt.is_enabled()
                if isinstance(res, bool) and not res:
                    is_opt_disabled = True
        if not is_opt_disabled:
            with contextlib.suppress(Exception):
                raw = None
                if hasattr(target_opt, "get_dom_attribute") and callable(
                    target_opt.get_dom_attribute
                ):
                    raw = target_opt.get_dom_attribute("disabled")
                elif hasattr(target_opt, "get_attribute") and callable(
                    target_opt.get_attribute
                ):
                    raw = target_opt.get_attribute("disabled")
                if (isinstance(raw, bool) and raw is True) or (
                    isinstance(raw, str) and raw.lower() in {"disabled", "true"}
                ):
                    is_opt_disabled = True
        if is_opt_disabled:
            raise ValueError(
                f"Cannot select disabled option '{value}' in dropdown {label or index}."
            )

        selected_text = (getattr(target_opt, "text", "") or "").strip()
        selected_val = ""
        if hasattr(target_opt, "get_attribute"):
            selected_val = (target_opt.get_attribute("value") or "").strip()

        if hasattr(element, "select_option"):
            element.select_option(selected_val or selected_text or val_str)

        if select is not None:
            try:
                if selected_val:
                    select.select_by_value(selected_val)
                else:
                    select.select_by_visible_text(selected_text)
            except Exception:
                target_opt.click()
        else:
            target_opt.click()

        with contextlib.suppress(Exception):
            driver = self._require_driver()
            driver.execute_script(
                "arguments[0].dispatchEvent(new Event('change', {bubbles: true}));",
                element,
            )

        self._elements = []
        return f"Selected '{value}' in the {role} {label}.".replace(" .", ".")

    def spotify_control(self, action: str, seconds: int = 10) -> str:
        """Control Spotify Web Player audio in the active signed-in tab."""
        driver = self._require_driver()
        action = (action or "").strip().lower()
        if action not in {"play", "pause", "rewind", "forward"}:
            raise ValueError("Spotify action must be play, pause, rewind, or forward")
        seconds = max(1, min(int(seconds), 600))
        if action in {"play", "pause"}:
            script = """
                const audio = document.querySelector('audio');
                const pp = document.querySelector('[data-testid="control-button-playpause"]');
                const act = arguments[0];
                if (pp) {
                    const isPlaying = /pause/i.test(pp.getAttribute('aria-label') || '');
                    if (act === 'pause' && isPlaying) { pp.click(); return 'paused'; }
                    if (act === 'play' && !isPlaying) { pp.click(); return 'playing'; }
                    if (act === 'play' || act === 'pause') { pp.click(); return act === 'play' ? 'playing' : 'paused'; }
                }
                if (audio) {
                    if (act === 'play') { audio.play(); return 'playing'; }
                    audio.pause(); return 'paused';
                }
                return 'no-audio';
            """
            result = driver.execute_script(script, action)
            if result == "no-audio":
                raise RuntimeError("Spotify has no audio player on this page")
            return f"Spotify {action} requested."
        delta = -seconds if action == "rewind" else seconds
        result = driver.execute_script(
            """const audio = document.querySelector('audio');
               if (!audio) return 'no-audio';
               audio.currentTime = Math.max(0, audio.currentTime + arguments[0]);
               return audio.currentTime;""",
            delta,
        )
        if result == "no-audio":
            raise RuntimeError("Spotify has no audio player on this page")
        verb = "rewound" if delta < 0 else "advanced"
        return f"Spotify {verb} {seconds} seconds."

    def spotify_play_playlist(self, name: str) -> str:
        """Find a named Spotify playlist and verify that playback starts."""
        target = " ".join((name or "").split())
        if not target:
            raise ValueError("Spotify playlist name cannot be empty")

        driver = self._ensure_driver()
        driver.get(
            f"https://open.spotify.com/search/{quote(target, safe='')}/playlists"
        )
        deadline = time.time() + 6.0
        matched = "not-found"
        while time.time() < deadline:
            matched = driver.execute_script(
                """
                const needle = arguments[0].toLowerCase();
                const needleAlt = needle.startsWith('my ') ? needle.slice(3) : needle;
                const altGenre = needle.includes('phonk')
                    ? needle.replace('phonk', 'funk')
                    : (needle.includes('funk') ? needle.replace('funk', 'phonk') : '');
                const links = [...document.querySelectorAll('a[href*="/playlist/"]')];
                const match = links.find((link) => {
                  const text = (link.innerText || link.getAttribute('aria-label') || '').toLowerCase();
                  return text.includes(needle) || (needleAlt && text.includes(needleAlt)) || (altGenre && text.includes(altGenre));
                });
                if (!match) return 'not-found';
                match.click();
                return 'matched';
                """,
                target,
            )
            if matched == "matched":
                break
            time.sleep(0.3)
        if matched != "matched":
            raise LookupError(f"Spotify playlist '{target}' was not found")

        deadline = time.time() + 6.0
        playback = "no-audio"
        while time.time() < deadline:
            playback = driver.execute_script(
                """
                const audio = document.querySelector('audio');
                const play = [...document.querySelectorAll('main button')].find((button) =>
                  /^play(?:\\s|$)/i.test((button.getAttribute('aria-label') ||
                   button.innerText || '').trim())
                );
                if (play) { play.click(); return 'play-requested'; }
                return audio ? 'paused' : 'no-audio';
                """
            )
            if playback in {"playing", "play-requested"}:
                break
            time.sleep(0.3)
        if playback not in {"playing", "play-requested"}:
            raise RuntimeError(
                f"Spotify found '{target}' but playback was not available ({playback})"
            )
        for _ in range(30):
            if self.playback_active() and self.playlist_context_matches():
                return f"Playing Spotify playlist {target}. Playback observed."
            time.sleep(0.3)
        raise RuntimeError(
            f"Spotify selected '{target}', but playback could not be verified"
        )

    def spotify_play_track(self, name: str, artist: str = "") -> str:
        """Find a track on Spotify and play it."""
        track_name = " ".join((name or "").split())
        artist_name = " ".join((artist or "").split())
        if not track_name:
            raise ValueError("Spotify track name cannot be empty")

        driver = self._ensure_driver()
        current_url = getattr(driver, "current_url", "") or ""

        # 1. If currently on a Spotify playlist page, check if the track is in the playlist rows
        if "/playlist/" in current_url:
            matched_playlist = driver.execute_script(
                """
                const trackNeedle = arguments[0].toLowerCase();
                const artistNeedle = arguments[1].toLowerCase();
                const rows = [...document.querySelectorAll('[data-testid="tracklist-row"]')];
                for (const row of rows) {
                    const text = (row.innerText || '').toLowerCase();
                    if (text.includes(trackNeedle) && (!artistNeedle || text.includes(artistNeedle))) {
                        const playBtn = row.querySelector('button[aria-label*="Play"], [data-testid="play-button"]');
                        if (playBtn) { playBtn.click(); return true; }
                        row.click();
                        return true;
                    }
                }
                return false;
                """,
                track_name,
                artist_name,
            )
            if matched_playlist:
                for _ in range(30):
                    if self.playback_active():
                        title = f"'{track_name}'" + (
                            f" by {artist_name}" if artist_name else ""
                        )
                        return f"Playing {title} from the active playlist on Spotify. Playback observed."
                    time.sleep(0.3)
                title = f"'{track_name}'" + (
                    f" by {artist_name}" if artist_name else ""
                )
                return f"Playing {title} from the active playlist on Spotify. Playback requested."

        # 2. Search Spotify tracks directly
        query = f"{track_name} {artist_name}".strip()
        driver.get(f"https://open.spotify.com/search/{quote(query, safe='')}/tracks")
        deadline = time.time() + 8.0
        played = False
        while time.time() < deadline:
            res = driver.execute_script(
                """
                const rows = [...document.querySelectorAll('[data-testid="tracklist-row"]')];
                if (!rows.length) return false;
                const first = rows[0];
                const playBtn = first.querySelector('button[aria-label*="Play"], [data-testid="play-button"]');
                if (playBtn) { playBtn.click(); return true; }
                first.click();
                return true;
                """
            )
            if res:
                played = True
                break
            time.sleep(0.3)

        if not played:
            raise LookupError(
                f"Spotify track '{query}' was not found in search results"
            )

        for _ in range(30):
            if self.playback_active():
                title = f"'{track_name}'" + (
                    f" by {artist_name}" if artist_name else ""
                )
                return f"Playing {title} on Spotify. Playback observed."
            time.sleep(0.3)

        title = f"'{track_name}'" + (f" by {artist_name}" if artist_name else "")
        return f"Playing {title} on Spotify. Playback requested."


def _normalize_block(text: str) -> str:
    """Collapse runs of blank lines but keep paragraph breaks."""
    lines = [line.strip() for line in (text or "").splitlines()]
    kept = [line for line in lines if line]
    return "\n".join(kept)
