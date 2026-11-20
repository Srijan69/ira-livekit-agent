"""Native Playwright browser control with stable observed targets and thread ownership."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from browser_runtime import ACTION_TIMEOUT, BrowserWorker, browser_call
from spotify_browser import SpotifyBrowser

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

# Browser waits are bounded because this host also serves a live voice loop.
PAGE_LOAD_TIMEOUT = 20

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
    # Keep text input separate from explicit keyboard actions.
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


@dataclass(frozen=True)
class PageElement:
    """One observed actionable control, numbered within the current page."""

    index: int
    role: str
    label: str

    def spoken(self) -> str:
        return f"{self.index}. {self.role} {self.label}".rstrip()


@dataclass
class _BrowserTarget:
    element: Any
    page: Any
    role: str
    label: str


class BrowserUnavailableError(RuntimeError):
    """The selected browser or its persistent profile cannot be opened."""


def _normalize(text: str) -> str:
    return " ".join((text or "").split())[:MAX_LABEL]


_ELEMENT_METADATA = """node => {
    const tag = node.tagName.toLowerCase();
    const type = (node.getAttribute('type') || 'text').toLowerCase();
    const role = node.getAttribute('role') ||
        (tag === 'input' ? (['submit','button','reset','image'].includes(type) ? 'button' :
        ['checkbox','radio'].includes(type) ? type : 'textbox') :
        ['textarea'].includes(tag) ? 'textbox' : tag === 'a' ? 'link' : tag);
    const labels = node.labels ? [...node.labels].map(l => l.innerText || l.textContent).join(' ') : '';
    const valueLabel = role === 'button' ? node.getAttribute('value') : '';
    return {tag, type, role, readonly: node.hasAttribute('readonly'),
        connected: node.isConnected,
        label: (node.innerText || '').trim() || node.getAttribute('aria-label') ||
        labels || node.getAttribute('placeholder') || valueLabel ||
        node.getAttribute('title') || node.getAttribute('name') || node.getAttribute('alt') || ''};
}"""


def _describe(element) -> tuple[str, str]:
    metadata = (
        element if isinstance(element, dict) else element.evaluate(_ELEMENT_METADATA)
    )
    return metadata.get("role", "element"), _normalize(metadata.get("label", ""))


def _browser_executable(config):
    selected = config["browser"]
    override = os.environ.get(f"IRA_{selected.upper()}_EXECUTABLE", "").strip()
    if override:
        if not os.path.isfile(override):
            raise BrowserUnavailableError(
                f"The configured {selected.title()} executable was not found."
            )
        return override
    if selected == "chrome":
        return (
            None  # Playwright's Chrome channel resolves the installed branded browser.
        )
    candidates = [
        os.path.join(
            os.environ.get(base, ""),
            "BraveSoftware",
            "Brave-Browser",
            "Application",
            "brave.exe",
        )
        for base in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")
    ]
    candidates.extend(
        filter(None, [shutil.which("brave"), shutil.which("brave-browser")])
    )
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    raise BrowserUnavailableError(
        "Brave is not installed at a supported location. Install Brave or set IRA_BRAVE_EXECUTABLE; no other browser will be opened."
    )


def _launch_options(config, headless):
    options = {
        "headless": headless,
        "timeout": PAGE_LOAD_TIMEOUT * 1000,
        "args": [
            f"--profile-directory={config['profile_directory']}",
            "--no-first-run",
            "--no-default-browser-check",
            "--start-maximized",
        ],
        # Installed browsers need their component updater to register Widevine.
        "ignore_default_args": ["--mute-audio", "--disable-component-update"],
        "viewport": {"width": 1280, "height": 900} if headless else None,
    }
    executable = _browser_executable(config)
    if executable:
        options["executable_path"] = executable
    else:
        options["channel"] = "chrome"
    return options


def _profile_lock_detected(config):
    return any(
        os.path.exists(os.path.join(config["user_data_dir"], name))
        for name in ("SingletonLock", "SingletonCookie", "SingletonSocket")
    )


class BrowserController(SpotifyBrowser):
    """Native Playwright pages and controls owned by one dedicated thread.

    Public methods remain synchronous for the existing host-action boundary.
    Playwright objects never leave the owner thread; request contextvars are
    copied onto it before each operation.
    """

    def __init__(self, *, headless=None, browser=None, playwright_factory=None):
        if headless is None:
            headless = os.environ.get("IRA_BROWSER_HEADLESS", "").lower() in {
                "1",
                "true",
                "yes",
            }
        self._headless = bool(headless)
        self._browser = (
            (browser or os.environ.get("IRA_BROWSER", "chrome")).strip().lower()
        )
        if self._browser not in {"chrome", "brave"}:
            raise ValueError("IRA_BROWSER must be chrome or brave")
        self._worker = BrowserWorker()
        self._playwright_factory = playwright_factory
        self._playwright = None
        self._context = None
        self._page = None
        self._elements = []
        self._tab_ids = {}
        self.input_guard = None

    def _check_action(self):
        if self.input_guard is not None:
            self.input_guard()

    def _pages(self):
        if self._context is None:
            return []
        try:
            return [page for page in self._context.pages if not page.is_closed()]
        except Exception:
            return []

    @property
    def is_open(self):
        if self._context is None:
            return False
        return self._worker.run(lambda: bool(self._pages()))

    @property
    def current_url(self):
        if self._context is None:
            return ""
        return self._worker.run(lambda: self._require_page().url)

    def _register_page(self, page):
        if page not in self._tab_ids:
            self._tab_ids[page] = "tab-" + uuid.uuid4().hex[:16]
            page.on("framenavigated", lambda _frame: self._clear_elements())
        return page

    def _clear_elements(self):
        self._elements = []

    @browser_call
    def invalidate_elements(self):
        self._clear_elements()

    def _ensure_page(self):
        self._check_action()
        pages = self._pages()
        if pages:
            if self._page not in pages:
                self._page = pages[-1]
                self._clear_elements()
            return self._page
        if self._context is not None:
            self._close_context()
        config = browser_config(self._browser)
        options = _launch_options(config, self._headless)
        self._check_action()
        try:
            if self._playwright_factory is None:
                from playwright.sync_api import sync_playwright

                self._playwright = sync_playwright().start()
            else:
                self._playwright = self._playwright_factory()
            self._check_action()
            self._context = self._playwright.chromium.launch_persistent_context(
                config["user_data_dir"], **options
            )
            self._context.set_default_timeout(ACTION_TIMEOUT)
            self._context.set_default_navigation_timeout(PAGE_LOAD_TIMEOUT * 1000)
            pages = self._pages()
            self._page = pages[0] if pages else self._context.new_page()
            for page in self._pages():
                self._register_page(page)
            self._context.on("page", self._register_page)
            logger.info(
                "Playwright browser started",
                extra={"browser": self._browser, "headless": self._headless},
            )
            return self._page
        except Exception as exc:
            self._close_context()
            self._check_action()
            if _profile_lock_detected(config):
                raise BrowserUnavailableError(
                    f"{self._browser.title()} is already using the configured profile. Close that IRA browser session before retrying."
                ) from exc
            raise BrowserUnavailableError(
                f"Could not start {self._browser.title()} with Playwright. Check the installed browser and IRA profile. No fallback browser was opened."
            ) from exc

    def _require_page(self):
        pages = self._pages()
        if not pages:
            raise BrowserUnavailableError("No page is open yet")
        if self._page not in pages:
            self._page = pages[-1]
            self._clear_elements()
        return self._page

    def _close_context(self):
        opened = self._context is not None
        context, runtime = self._context, self._playwright
        self._context = self._playwright = self._page = None
        self._elements = []
        self._tab_ids = {}
        try:
            if context is not None:
                context.close()
        finally:
            if runtime is not None:
                runtime.stop()
        return "Browser closed." if opened else "The browser was not open."

    def close(self):
        if self._worker.is_owner:
            return self._close_context()
        try:
            return self._worker.run(self._close_context)
        finally:
            self._worker.close()

    @browser_call
    def navigate(self, url):
        target = (url or "").strip()
        if not target:
            raise ValueError("No address was given.")
        if not urlsplit(target).scheme:
            target = "https://" + target
        page = self._ensure_page()
        self._check_action()
        try:
            page.goto(target, wait_until="domcontentloaded")
        except Exception:
            raise RuntimeError(
                "Browser navigation did not finish successfully. Inspect the current page before continuing; no fallback browser was opened."
            ) from None
        finally:
            self._clear_elements()
        return self.describe_page()

    @browser_call
    def open_spotify(self, browser=None):
        selected = (browser or self._browser).strip().lower()
        if selected not in {"chrome", "brave"}:
            raise ValueError("Browser must be chrome or brave")
        self._check_action()
        if selected != self._browser:
            self._close_context()
        self._check_action()
        self._browser = selected
        return self.navigate("https://open.spotify.com")

    @browser_call
    def back(self):
        page = self._require_page()
        self._check_action()
        try:
            page.go_back(wait_until="domcontentloaded")
        finally:
            self._clear_elements()
        return self.describe_page()

    @browser_call
    def new_tab(self, url=""):
        self._ensure_page()
        self._check_action()
        page = self._context.new_page()
        self._page = self._register_page(page)
        self._clear_elements()
        if url and url.strip():
            self.navigate(url)
        number = self._pages().index(page) + 1
        return f"Opened tab {number}: {_normalize(page.title()) or 'New tab'}."

    def _resolve_tab(self, tab, *, current=False):
        pages = self._pages()
        if not pages:
            raise BrowserUnavailableError("No browser tabs are currently open.")
        if tab is None and current:
            return self._require_page()
        if isinstance(tab, int) and not isinstance(tab, bool):
            if not 1 <= tab <= len(pages):
                raise IndexError(
                    f"Tab {tab} does not exist. There are {len(pages)} open tabs."
                )
            return pages[tab - 1]
        if isinstance(tab, str):
            for page in pages:
                if self._tab_ids.get(page) == tab:
                    return page
            if tab.strip().isdigit():
                return self._resolve_tab(int(tab.strip()))
            raise ValueError(
                f"Tab '{tab}' was not found. Open tabs: 1 to {len(pages)}."
            )
        raise TypeError("Tab identifier must be an integer or stable tab ID string.")

    @browser_call
    def switch_tab(self, tab):
        page = self._resolve_tab(tab)
        self._check_action()
        page.bring_to_front()
        self._page = page
        self._clear_elements()
        return self.describe_page()

    @browser_call
    def list_tabs(self):
        pages = self._pages()
        result = []
        for index, page in enumerate(pages, 1):
            self._register_page(page)
            result.append(
                {
                    "index": index,
                    "title": _normalize(page.title()) or "Untitled page",
                    "url": page.url,
                    "is_active": page == self._page,
                    "handle": self._tab_ids[page],
                }
            )
        return result

    @browser_call
    def close_tab(self, tab=None):
        if not self._pages():
            return self._close_context()
        page = self._resolve_tab(tab, current=True)
        pages = self._pages()
        index = pages.index(page)
        active = page == self._page
        self._check_action()
        page.close()
        self._tab_ids.pop(page, None)
        remaining = self._pages()
        if not remaining:
            self._close_context()
            return f"Closed tab {index + 1}. Browser closed as no tabs remain."
        if active:
            self._page = remaining[min(max(0, index - 1), len(remaining) - 1)]
            self._page.bring_to_front()
        self._clear_elements()
        return f"Closed tab {index + 1}. {self.describe_page()}"

    @browser_call
    def evaluate(self, expression, arg=None):
        """Run fixed internal inspection code on the browser owner thread."""
        return self._require_page().evaluate(expression, arg)

    @browser_call
    def page_html(self):
        return self._require_page().content()

    @browser_call
    def describe_page(self):
        page = self._require_page()
        count = len(self.list_elements())
        return (
            f"{_normalize(page.title()) or 'Untitled page'}. {count} things to act on."
        )

    @browser_call
    def read_page(self):
        try:
            text = _normalize_block(self._require_page().locator("body").inner_text())
        except Exception:
            raise RuntimeError("Could not read that page.") from None
        if not text:
            return "That page has no readable text."
        if len(text) > MAX_PAGE_TEXT:
            return text[:MAX_PAGE_TEXT] + " ... (truncated)"
        return text

    @browser_call
    def observe(self, kind, value):
        page = self._require_page()
        if kind == "page_contains":
            return value.casefold() in page.locator("body").inner_text().casefold()
        if kind == "page_element":
            validate_selector(value)
            return any(
                element.is_visible()
                for element in page.locator(value).element_handles()
            )
        if kind == "page_value":
            selector, expected = parse_page_value(value)
            element = self._unique_selector(selector, enabled=False)
            self._validate_value_field(element, writable=False)
            return element.input_value() == expected
        raise ValueError(f"Unsupported browser check: {kind}")

    @browser_call
    def list_elements(self):
        page = self._require_page()
        described, found = [], []
        for frame in page.frames:
            if frame != page.main_frame:
                try:
                    if not frame.frame_element().is_visible():
                        continue
                except Exception:
                    continue
            for element in frame.locator(_INTERACTIVE_SELECTOR).element_handles():
                if len(found) >= MAX_ELEMENTS:
                    break
                try:
                    if not element.is_visible():
                        continue
                    role, label = _describe(element)
                except Exception:
                    continue
                found.append(_BrowserTarget(element, page, role, label))
                described.append(PageElement(len(found), role, label))
            if len(found) >= MAX_ELEMENTS:
                break
        self._elements = found
        return described

    def _assert_action_url(self, expected_url):
        expected = canonical_action_url(expected_url)
        try:
            current = canonical_action_url(self._require_page().url)
        except Exception:
            raise ValueError("Current browser URL cannot be verified.") from None
        if current != expected:
            raise ValueError("Current browser URL does not match the expected URL.")

    def _unique_selector(self, selector, *, enabled=True):
        validate_selector(selector)
        matches = [
            element
            for element in self._require_page().locator(selector).element_handles()
            if element.is_visible() and (not enabled or element.is_enabled())
        ]
        if len(matches) != 1:
            raise ValueError(
                "Browser selector must match exactly one visible, eligible target."
            )
        return matches[0]

    @staticmethod
    def _validate_value_field(element, *, writable):
        metadata = element.evaluate(
            "(node) => ({tag:node.tagName.toLowerCase(),type:(node.getAttribute('type')||'text').toLowerCase(),readonly:node.hasAttribute('readonly')})"
        )
        if metadata["tag"] == "select":
            if writable:
                raise ValueError(
                    "Select dropdown elements cannot be typed into; use select_dropdown instead."
                )
            return
        if metadata["tag"] not in {"input", "textarea"} or (
            metadata["tag"] == "input" and metadata["type"] not in _ALLOWED_INPUT_TYPES
        ):
            raise ValueError(
                "Only ordinary text, date, and select fields can be read or typed into; password, file, and hidden fields are not supported."
            )
        if writable and metadata["readonly"]:
            raise ValueError("Cannot type into a read-only field.")

    def _resolve(self, index):
        if type(index) is not int or not 1 <= index <= len(self._elements):
            raise IndexError(
                f"There is no current element reference {index}. Call list_page_elements first; the page has {len(self._elements)} observed controls."
            )
        target = self._elements[index - 1]
        try:
            valid = target.page == self._require_page() and target.element.evaluate(
                "(node)=>node.isConnected"
            )
            role, label = _describe(target.element)
            valid = valid and role == target.role and label == target.label
        except Exception:
            valid = False
        if not valid:
            self._clear_elements()
            raise RuntimeError(
                "The observed browser target changed or became stale. List the current controls before input."
            )
        return target

    def _native_click(self, element, *, preflight=None):
        observed = element.evaluate(_ELEMENT_METADATA)
        identity = _describe(observed)
        kind = [observed["tag"], observed["type"]]

        def validate_target():
            if preflight is not None:
                preflight()
            if (
                not element.evaluate("(node)=>node.isConnected")
                or _describe(element) != identity
                or not element.is_visible()
                or not element.is_enabled()
            ):
                raise RuntimeError("The browser control changed before input.")

        self._check_action()
        # Trial clicks can trigger hover layout; settle it before final preflight.
        element.hover(timeout=ACTION_TIMEOUT)
        self._check_action()
        element.click(trial=True, timeout=ACTION_TIMEOUT)
        self._check_action()
        validate_target()
        page = self._require_page()
        frame = element.owner_frame()
        ordinary_root = element.evaluate(
            "(node)=>node.getRootNode()===node.ownerDocument"
        )
        if frame is None or type(ordinary_root) is not bool:
            raise RuntimeError("The browser target's document cannot be verified.")
        if frame != page.main_frame or not ordinary_root:
            # Playwright retains its ancestor-frame and shadow-root hit guards.
            self._check_action()
            element.click(timeout=500)
            return
        first = element.bounding_box()
        viewport = page.evaluate("() => ({width:innerWidth,height:innerHeight})")
        second = element.bounding_box()
        keys = ("x", "y", "width", "height")
        if (
            not isinstance(first, dict)
            or not isinstance(second, dict)
            or first != second
            or any(
                type(second.get(key)) not in {int, float}
                or not math.isfinite(second[key])
                for key in keys
            )
            or not isinstance(viewport, dict)
            or any(
                type(viewport.get(key)) not in {int, float}
                or not math.isfinite(viewport[key])
                or viewport[key] <= 0
                for key in ("width", "height")
            )
            or second["width"] <= 0
            or second["height"] <= 0
            or second["x"] < 0
            or second["y"] < 0
            or second["x"] + second["width"] > viewport["width"]
            or second["y"] + second["height"] > viewport["height"]
        ):
            raise RuntimeError("No stable browser click point can be verified.")
        point = {
            "x": second["x"] + second["width"] / 2,
            "y": second["y"] + second["height"] / 2,
        }
        point_proof = (
            "(node,[point,expected,identity,kind]) => {"
            "const metadata = (" + _ELEMENT_METADATA + ")(node);"
            "const label = (metadata.label||'').trim().replace(/\\s+/g,' ').slice(0,"
            + str(MAX_LABEL)
            + ");"
            + """
            const rect = node.getBoundingClientRect();
            const hit = document.elementFromPoint(point.x,point.y);
            return metadata.connected && metadata.role===identity[0] && label===identity[1] &&
                metadata.tag===kind[0] && metadata.type===kind[1] &&
                !node.matches(':disabled') && !node.closest('[aria-disabled="true"]') &&
                node.getRootNode()===node.ownerDocument &&
                rect.x===expected.x && rect.y===expected.y &&
                rect.width===expected.width && rect.height===expected.height &&
                (hit===node || (hit!==null && node.contains(hit)));
        }"""
        )
        proof_args = [point, second, identity, kind]
        if element.evaluate(point_proof, proof_args) is not True:
            raise RuntimeError(
                "The browser click point is covered or cannot be verified."
            )
        validate_target()
        # Keep metadata, bounds and hit ownership in one final DOM read.
        if element.evaluate(point_proof, proof_args) is not True:
            raise RuntimeError("The browser click point changed before input.")
        self._check_action()
        # Native input has no second animation-frame wait after the final guard.
        page.mouse.click(point["x"], point["y"])

    @browser_call
    def click(self, index):
        target = self._resolve(index)
        try:
            self._native_click(target.element)
        except Exception:
            self._check_action()
            raise RuntimeError(
                "Browser click failed; the observed control may be covered or changed. Inspect the screen before retrying."
            ) from None
        finally:
            self._clear_elements()
        return f"Clicked the {target.role} {target.label}.".replace(" .", ".")

    @browser_call
    def click_selector(self, selector, expected_url):
        validate_selector(selector)
        self._assert_action_url(expected_url)
        element = self._unique_selector(selector)
        self._assert_action_url(expected_url)

        def preflight():
            self._assert_action_url(expected_url)
            current = self._unique_selector(selector)
            if not element.evaluate("(node,other)=>node===other", current):
                raise RuntimeError("The browser selector target changed before input.")

        try:
            self._native_click(element, preflight=preflight)
        except Exception:
            self._check_action()
            raise RuntimeError(
                "Browser click failed; the target may have changed or be covered."
            ) from None
        finally:
            self._clear_elements()

    def _fill(self, element, text, *, preflight=None):
        identity = _describe(element)
        element.wait_for_element_state("editable", timeout=ACTION_TIMEOUT)
        self._check_action()
        self._validate_value_field(element, writable=True)
        if (
            not element.is_enabled()
            or not element.evaluate("(node)=>node.isConnected")
            or _describe(element) != identity
        ):
            raise RuntimeError("The input control changed while waiting for input.")
        if preflight is not None:
            preflight()
        expected = element.evaluate(
            """(node,value) => {
            if (!['date','datetime-local','month','time'].includes(node.type)) return value;
            const field = document.createElement('input');
            field.type = node.type;
            field.value = value;
            return field.value;
        }""",
            text,
        )
        if text and not expected:
            raise ValueError("The requested field value is malformed.")
        self._check_action()
        element.fill(expected, timeout=500)
        self._check_action()
        if (
            not element.evaluate("(node)=>node.isConnected")
            or _describe(element) != identity
            or element.input_value() != expected
        ):
            raise RuntimeError("Input value could not be verified.")

    @browser_call
    def type_text(self, index, text, *, submit=False):
        validate_browser_text(text)
        if type(submit) is not bool:
            raise ValueError("submit must be a boolean.")
        target = self._resolve(index)
        self._validate_value_field(target.element, writable=True)
        try:
            self._fill(target.element, text)
            if submit:
                self._check_action()
                target.element.press("Enter", timeout=500)
        except Exception:
            self._check_action()
            raise RuntimeError(
                "Browser input could not be verified or submitted."
            ) from None
        finally:
            self._clear_elements()
        suffix = " and submitted" if submit else ""
        return f"Typed into the {target.role} {target.label}{suffix}.".replace(
            " .", "."
        )

    @browser_call
    def type_selector(self, selector, text, expected_url, *, submit=False):
        validate_selector(selector)
        validate_browser_text(text)
        if type(submit) is not bool:
            raise ValueError("submit must be a boolean.")
        self._assert_action_url(expected_url)
        element = self._unique_selector(selector)
        self._validate_value_field(element, writable=True)
        self._assert_action_url(expected_url)

        def preflight():
            self._assert_action_url(expected_url)
            current = self._unique_selector(selector)
            if not element.evaluate("(node,other)=>node===other", current):
                raise RuntimeError("Input target changed while waiting for input.")

        try:
            self._check_action()
            element.fill("", timeout=ACTION_TIMEOUT)
            self._check_action()
            self._assert_action_url(expected_url)
            current = self._unique_selector(selector)
            if not element.evaluate("(node,other)=>node===other", current):
                raise RuntimeError("Input target changed after clearing.")
            self._validate_value_field(current, writable=True)
            self._fill(current, text, preflight=preflight)
            self._assert_action_url(expected_url)
            current = self._unique_selector(selector)
            if not element.evaluate("(node,other)=>node===other", current):
                raise RuntimeError("Input target changed before verification.")
            if submit:
                self._check_action()
                self._assert_action_url(expected_url)
                current.press("Enter", timeout=500)
        except Exception:
            self._check_action()
            raise RuntimeError(
                "Browser input could not be verified or submitted."
            ) from None
        finally:
            self._clear_elements()

    @browser_call
    def select_dropdown(self, index, value):
        target = self._resolve(index)
        element = target.element
        metadata = element.evaluate(
            "(node)=>({tag:node.tagName.toLowerCase(),disabled:node.disabled})"
        )
        if metadata["tag"] != "select":
            raise ValueError(f"Element {index} is not a select dropdown.")
        if metadata["disabled"] or not element.is_enabled():
            raise ValueError("Cannot select an option from a disabled dropdown.")
        options = element.evaluate(
            """node=>[...node.options].map((o,index)=>({index,text:o.text.trim(),value:o.value.trim(),disabled:o.disabled||(o.parentElement.tagName==='OPTGROUP'&&o.parentElement.disabled)}))"""
        )
        query = str(value).strip()
        matches = [o for o in options if o["value"] == query]
        if not matches:
            matches = [o for o in options if o["text"] == query]
        if not matches:
            matches = [
                o
                for o in options
                if o["value"].casefold() == query.casefold()
                or o["text"].casefold() == query.casefold()
            ]
        if not matches:
            available = ", ".join(o["text"] or o["value"] for o in options[:8])
            raise ValueError(
                f"Option '{value}' not found. Available options: {available or 'none'}."
            )
        if len(matches) != 1:
            raise ValueError("That option is ambiguous; specify its unique value.")
        selected = matches[0]
        if selected["disabled"]:
            raise ValueError("Cannot select a disabled option.")
        try:
            self._check_action()
            element.select_option(index=selected["index"], timeout=ACTION_TIMEOUT)
            self._check_action()
            if (
                not element.evaluate("(node)=>node.isConnected")
                or element.evaluate("(node)=>node.selectedIndex") != selected["index"]
            ):
                raise RuntimeError("Selected option could not be verified.")
        finally:
            self._clear_elements()
        return f"Selected '{value}' in the {target.role} {target.label}.".replace(
            " .", "."
        )


def _normalize_block(text):
    return "\n".join(line.strip() for line in (text or "").splitlines()).strip()
