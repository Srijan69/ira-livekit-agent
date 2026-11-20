"""Native Playwright tab, form, and dropdown behavior regressions."""

import pytest

from agent import Assistant
from browser import BrowserController
from request_scope_livekit import _OBSERVATIONS
from skill_tools import CAPABILITIES
from skills import DETERMINISTIC_TOOLS
from tests.fixtures.playwright_fakes import (
    FakeContext,
    FakeElement,
    FakeOption,
    FakePage,
    FakeSelectElement,
    controller_with,
)


def test_new_tab_and_navigation_keep_stable_ids():
    context = FakeContext()
    browser = controller_with(context=context)
    first = browser.list_tabs()[0]["handle"]
    assert "Opened tab 2" in browser.new_tab("https://example.com/profile")
    tabs = browser.list_tabs()
    assert len(tabs) == 2 and tabs[0]["handle"] == first
    assert tabs[1]["url"] == "https://example.com/profile"
    assert tabs[1]["is_active"]
    browser.switch_tab(first)
    assert browser.list_tabs()[0]["is_active"]
    browser.close()


@pytest.mark.parametrize("identifier", [2, "2", " 2 ", "stable"])
def test_switch_tab_identifiers(identifier):
    pages = [FakePage(title="Form"), FakePage(title="Profile")]
    context = FakeContext(pages)
    browser = controller_with(context=context)
    if identifier == "stable":
        identifier = browser.list_tabs()[1]["handle"]
    assert "Profile" in browser.switch_tab(identifier)
    assert context.active_page is pages[1]
    assert "Form" in browser.switch_tab(1)
    browser.close()


@pytest.mark.parametrize("method", ["switch_tab", "close_tab"])
@pytest.mark.parametrize(
    "identifier,exception",
    [
        (-1, IndexError),
        (0, IndexError),
        (3, IndexError),
        ("0", IndexError),
        (" 3 ", IndexError),
        ("invalid", ValueError),
        ("", ValueError),
        ("-1", ValueError),
        ([1], TypeError),
        (True, TypeError),
        (False, TypeError),
        (1.5, TypeError),
        ({"tab": 1}, TypeError),
    ],
)
def test_invalid_tab_identifiers_have_no_effect(method, identifier, exception):
    context = FakeContext([FakePage(), FakePage()])
    browser = controller_with(context=context)
    before = browser.list_tabs()
    with pytest.raises(exception):
        getattr(browser, method)(identifier)
    assert browser.list_tabs() == before
    browser.close()


def test_tab_listing_does_not_change_focus_and_closed_ids_do_not_reuse():
    context = FakeContext([FakePage(title="Form"), FakePage(title="Profile")])
    browser = controller_with(context=context)
    tabs = browser.list_tabs()
    assert [t["title"] for t in tabs] == ["Form", "Profile"]
    assert [t["is_active"] for t in tabs] == [True, False]
    assert context.active_page is context.pages[0]
    stale_id = tabs[1]["handle"]
    browser.close_tab(stale_id)
    assert browser.list_tabs()[0]["handle"] == tabs[0]["handle"]
    with pytest.raises(ValueError):
        browser.switch_tab(stale_id)
    browser.new_tab()
    assert browser.list_tabs()[1]["handle"] != stale_id
    browser.close()


@pytest.mark.parametrize("background", [True, False])
def test_close_tab_preserves_or_moves_focus(background):
    pages = [FakePage(title="Form"), FakePage(title="Profile")]
    context = FakeContext(pages)
    browser = controller_with(context=context)
    if not background:
        browser.switch_tab(2)
    assert "Closed tab 2" in browser.close_tab(2)
    assert pages[1].closed and not pages[0].closed
    assert browser.list_tabs()[0]["is_active"]
    assert "Browser closed" in browser.close_tab()
    assert context.closed and not browser.is_open
    assert browser.list_tabs() == []
    assert browser.close_tab() == "The browser was not open."
    browser.close()


def test_closed_browser_observations():
    browser = BrowserController(headless=True)
    assert browser.list_tabs() == []
    assert browser.close_tab() == "The browser was not open."
    browser.close()


def test_tab_changes_invalidate_old_element_references():
    button = FakeElement("button", text="Submit")
    browser = controller_with(context=FakeContext([FakePage([button]), FakePage([])]))
    browser.list_elements()
    browser.switch_tab(2)
    with pytest.raises(IndexError):
        browser.click(1)
    assert not button.clicked
    browser.switch_tab(1)
    browser.click(1)
    assert button.clicked
    with pytest.raises(IndexError):
        browser.click(1)
    browser.new_tab()
    assert browser._elements == []
    browser.close()


@pytest.mark.parametrize(
    "kind,value",
    [
        ("date", "1995-05-15"),
        ("datetime-local", "2024-06-15T14:30"),
        ("month", "2024-01"),
        ("time", "15:30"),
    ],
)
def test_native_form_controls(kind, value):
    field = FakeElement("input", type=kind, name="birth_date")
    browser = controller_with(FakePage([field]))
    browser.list_elements()
    assert "Typed into the textbox" in browser.type_text(1, value)
    assert field.input_value() == value
    assert field.actions == [("fill", value)]
    assert browser._elements == []
    browser.close()


@pytest.mark.parametrize("query", ["Engineering", "eng", "engineering", "ENG"])
def test_select_dropdown_visible_label_and_value(query):
    field = FakeSelectElement()
    browser = controller_with(FakePage([field]))
    browser.list_elements()
    assert "Selected" in browser.select_dropdown(1, query)
    assert field.selected_value == "eng"
    assert browser._elements == []
    browser.close()


@pytest.mark.parametrize("by_value", [True, False])
def test_disabled_option_is_rejected_without_changing_selection(by_value):
    field = FakeSelectElement(
        options=[
            FakeOption("Engineering", "eng"),
            FakeOption("Deprecated", "old", disabled=True),
        ]
    )
    browser = controller_with(FakePage([field]))
    browser.list_elements()
    with pytest.raises(ValueError, match="disabled option"):
        browser.select_dropdown(1, "old" if by_value else "Deprecated")
    assert field.selected_value == "eng" and field.actions == []
    browser.close()


def test_disabled_dropdown_is_rejected():
    field = FakeSelectElement(disabled=True)
    browser = controller_with(FakePage([field]))
    browser.list_elements()
    with pytest.raises(ValueError, match="disabled dropdown"):
        browser.select_dropdown(1, "Engineering")
    assert field.actions == []
    browser.close()


def test_wrong_control_and_missing_option_are_rejected():
    field = FakeSelectElement()
    browser = controller_with(FakePage([field, FakeElement("input")]))
    browser.list_elements()
    with pytest.raises(ValueError, match="cannot be typed"):
        browser.type_text(1, "anything")
    with pytest.raises(ValueError, match="not a select"):
        browser.select_dropdown(2, "eng")
    with pytest.raises(ValueError, match="Available options"):
        browser.select_dropdown(1, "missing")
    browser.close()


@pytest.mark.asyncio
async def test_assistant_browser_tools_dispatch_and_scope(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    assistant = Assistant()
    tool_names = {t.info.name for t in assistant.tools}

    expected = {
        "new_tab",
        "switch_tab",
        "list_tabs",
        "close_tab",
        "select_dropdown_option",
    }
    assert expected <= tool_names

    # Direct method alias
    assert hasattr(assistant, "select_dropdown")

    # Verify CAPABILITIES registration
    assert expected <= set(CAPABILITIES["browser"])

    # Verify RequestScope classification
    assert "list_tabs" in _OBSERVATIONS
    assert "new_tab" not in _OBSERVATIONS
    assert "switch_tab" not in _OBSERVATIONS
    assert "close_tab" not in _OBSERVATIONS
    assert "select_dropdown_option" not in _OBSERVATIONS

    # Verify DETERMINISTIC_TOOLS registration
    assert expected <= DETERMINISTIC_TOOLS
