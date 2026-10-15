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

import logging
import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import quote

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
        return self._driver is not None

    def _ensure_driver(self) -> WebDriver:
        """Start Chrome on first use and reuse it after.

        The import is function-local for the same reason `pyautogui` is in
        `agent.py`: this file is deployed to LiveKit Cloud, where there is no
        display. A module-scope import of a browser stack that cannot run there
        would take the whole agent down instead of just this one tool.
        """
        if self._driver is not None:
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
            return "No address was given."
        # Speech rarely includes a scheme -- "go to hacker news" arrives as a
        # bare host. Assume https, which is also what a browser's address bar
        # does now.
        if "://" not in target:
            target = f"https://{target}"

        driver = self._ensure_driver()
        driver.get(target)
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

    def _require_driver(self) -> WebDriver:
        if self._driver is None:
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
            return "Could not read that page."

        if not text:
            return "That page has no readable text."
        if len(text) > MAX_PAGE_TEXT:
            return text[:MAX_PAGE_TEXT] + " ... (truncated)"
        return text

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
        element.clear()
        element.send_keys(text)
        if submit:
            element.send_keys(Keys.RETURN)
            self._elements = []
            return f"Typed into the {role} {label} and submitted.".replace(" .", ".")
        return f"Typed into the {role} {label}.".replace(" .", ".")

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
                if (!audio) return 'no-audio';
                if (arguments[0] === 'play') { audio.play(); return 'playing'; }
                audio.pause(); return 'paused';
            """
            driver.execute_script(script, action)
            return f"Spotify {action} requested."
        delta = -seconds if action == "rewind" else seconds
        driver.execute_script(
            """const audio = document.querySelector('audio');
               if (!audio) return 'no-audio';
               audio.currentTime = Math.max(0, audio.currentTime + arguments[0]);
               return audio.currentTime;""",
            delta,
        )
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
        # Spotify renders search results client-side. The call already runs in
        # a worker thread, so a short bounded wait does not block voice audio.
        time.sleep(0.8)
        matched = driver.execute_script(
            """
            const needle = arguments[0].toLowerCase();
            const links = [...document.querySelectorAll('a[href*="/playlist/"]')];
            const match = links.find((link) =>
              (link.innerText || link.getAttribute('aria-label') || '')
                .toLowerCase().includes(needle)
            );
            if (!match) return 'not-found';
            match.click();
            return 'matched';
            """,
            target,
        )
        if matched != "matched":
            raise LookupError(f"Spotify playlist '{target}' was not found")

        time.sleep(0.8)
        playback = driver.execute_script(
            """
            const audio = document.querySelector('audio');
            if (audio && !audio.paused) return 'playing';
            const play = [...document.querySelectorAll('button')].find((button) =>
              ((button.getAttribute('aria-label') || '') + ' ' +
               (button.innerText || '')).toLowerCase().includes('play')
            );
            if (play) { play.click(); return 'play-requested'; }
            return audio ? 'paused' : 'no-audio';
            """
        )
        if playback not in {"playing", "play-requested"}:
            raise RuntimeError(
                f"Spotify found '{target}' but playback was not available ({playback})"
            )
        return f"Playing Spotify playlist {target}. Playback {playback}."


def _normalize_block(text: str) -> str:
    """Collapse runs of blank lines but keep paragraph breaks."""
    lines = [line.strip() for line in (text or "").splitlines()]
    kept = [line for line in lines if line]
    return "\n".join(kept)
