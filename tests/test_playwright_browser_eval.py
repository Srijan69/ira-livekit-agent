"""Real isolated Chromium checks for native browser actions and references."""

import os
from urllib.parse import quote

import pytest

from browser import BrowserController

pytestmark = pytest.mark.skipif(
    os.environ.get("IRA_RUN_BROWSER_EVALS") != "1",
    reason="Real Playwright evaluation is opt-in",
)


@pytest.fixture(scope="module")
def real_browser(tmp_path_factory):
    directory = tmp_path_factory.mktemp("playwright-profile") / "Default"
    previous = os.environ.get("IRA_BROWSER_PROFILE")
    os.environ["IRA_BROWSER_PROFILE"] = str(directory)
    browser = BrowserController(headless=True, browser="chrome")
    try:
        yield browser
    finally:
        browser.close()
        if previous is None:
            os.environ.pop("IRA_BROWSER_PROFILE", None)
        else:
            os.environ["IRA_BROWSER_PROFILE"] = previous


def load(browser, body):
    browser.navigate("data:text/html;charset=utf-8," + quote(body))


def test_replaced_numbered_node_cannot_receive_input(real_browser):
    load(real_browser, '<button onclick="window.clicked=true">Save</button>')
    real_browser.list_elements()
    real_browser.evaluate(
        '() => document.querySelector("button").outerHTML="<button onclick=\\"window.clicked=true\\">Delete</button>"'
    )
    with pytest.raises(Exception, match=r"changed|stale|reference|attached"):
        real_browser.click(1)
    assert real_browser.evaluate("() => window.clicked || false") is False


def test_covered_control_does_not_receive_a_forced_click(real_browser):
    load(
        real_browser,
        '<button onclick="window.clicked=true">Delete</button>'
        '<div style="position:fixed;inset:0;z-index:99;background:white">Dialog</div>',
    )
    real_browser.list_elements()
    with pytest.raises(Exception, match=r"covered|click|action|timeout"):
        real_browser.click(1)
    assert real_browser.evaluate("() => window.clicked || false") is False


def test_native_click_is_trusted_and_invalidates_the_reference(real_browser):
    load(real_browser, '<button onclick="window.trusted=event.isTrusted">Save</button>')
    real_browser.list_elements()
    assert "Clicked" in real_browser.click(1)
    assert real_browser.evaluate("() => window.trusted") is True
    with pytest.raises(IndexError, match=r"list|List|reference"):
        real_browser.click(1)


def test_native_fill_and_enter_submit_the_observed_field(real_browser):
    load(
        real_browser,
        "<form onsubmit=\"event.preventDefault();window.value=document.querySelector('input').value\">"
        '<label for="search">Search</label><input id="search"></form>',
    )
    real_browser.list_elements()
    real_browser.type_text(1, "Telugu songs", submit=True)
    assert real_browser.evaluate("() => window.value") == "Telugu songs"


@pytest.mark.parametrize(
    "kind,value",
    [
        ("date", "2024-02-29"),
        ("datetime-local", "2024-06-15T14:30"),
        ("datetime-local", "2024-06-15T14:30:00"),
        ("month", "2024-06"),
        ("time", "14:30"),
        ("time", "14:30:00"),
    ],
)
def test_native_fill_accepts_date_and_time_fields(real_browser, kind, value):
    load(real_browser, f'<input type="{kind}" aria-label="When">')
    real_browser.list_elements()
    real_browser.type_text(1, value)
    expected = (
        value.removesuffix(":00")
        if kind == "datetime-local" and value.endswith(":00")
        else value
    )
    assert (
        real_browser.evaluate("() => document.querySelector('input').value") == expected
    )


def test_invalid_date_does_not_claim_success(real_browser):
    load(real_browser, '<input type="date" aria-label="When">')
    real_browser.list_elements()
    with pytest.raises(Exception, match=r"input|value|valid|field"):
        real_browser.type_text(1, "2023-02-29")


def test_dropdown_selection_and_disabled_option(real_browser):
    load(
        real_browser,
        '<select aria-label="Language"><option value="en">English</option><option value="te">Telugu</option><option value="x" disabled>Unavailable</option></select>',
    )
    real_browser.list_elements()
    real_browser.select_dropdown(1, "telugu")
    assert real_browser.evaluate("() => document.querySelector('select').value") == "te"
    real_browser.list_elements()
    with pytest.raises(ValueError, match="disabled"):
        real_browser.select_dropdown(1, "Unavailable")
    assert real_browser.evaluate("() => document.querySelector('select').value") == "te"


def test_tabs_keep_stable_identifiers_and_background_close_preserves_focus(
    real_browser,
):
    real_browser.close()
    load(real_browser, "<title>First</title><button>One</button>")
    first = real_browser.list_tabs()[0]["handle"]
    real_browser.new_tab(
        "data:text/html," + quote("<title>Second</title><button>Two</button>")
    )
    tabs = real_browser.list_tabs()
    second = tabs[1]["handle"]
    assert tabs[1]["is_active"] and not tabs[0]["is_active"]
    assert second != first
    real_browser.close_tab(first)
    assert real_browser.list_tabs()[0]["handle"] == second
    assert real_browser.list_tabs()[0]["is_active"]


def test_stop_after_actionability_check_prevents_the_click(real_browser):
    load(real_browser, '<button onclick="window.clicked=true">Save</button>')
    real_browser.list_elements()
    calls = 0

    def guard():
        nonlocal calls
        calls += 1
        if calls >= 3:
            raise RuntimeError("stopped")

    real_browser.input_guard = guard
    try:
        with pytest.raises(RuntimeError, match="stopped"):
            real_browser.click(1)
    finally:
        real_browser.input_guard = None
    assert real_browser.evaluate("() => window.clicked || false") is False


def test_numbered_controls_include_visible_frames_with_trusted_input(real_browser):
    load(
        real_browser,
        "<iframe srcdoc=\"<button onclick='window.clicked=event.isTrusted'>Inside frame</button>\"></iframe>"
        '<iframe style="display:none" srcdoc="<button>Hidden frame</button>"></iframe>',
    )
    elements = real_browser.list_elements()
    assert [element.label for element in elements] == ["Inside frame"]
    real_browser.click(1)
    assert (
        real_browser._worker.run(
            lambda: real_browser._page.frames[1].evaluate("() => window.clicked")
        )
        is True
    )
