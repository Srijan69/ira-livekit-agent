"""Tests for multi-tab browser management and enhanced form controls."""

from __future__ import annotations

import pytest

from agent import Assistant
from browser import BrowserController
from request_scope_livekit import _OBSERVATIONS
from skill_tools import CAPABILITIES
from skills import DETERMINISTIC_TOOLS

# ---------------------------------------------------------------------------
# Test Doubles
# ---------------------------------------------------------------------------


class FakeElement:
    def __init__(self, tag: str, *, text: str = "", displayed: bool = True, **attrs):
        self.tag_name = tag
        self._text = text
        self._displayed = displayed
        self._attrs = attrs
        self.clicked = False
        self.typed: list[str] = []
        self.cleared = False
        self.selected_value: str | None = None

    @property
    def text(self) -> str:
        return self._text

    @text.setter
    def text(self, value: str) -> None:
        self._text = value

    def get_attribute(self, name: str):
        return self._attrs.get(name.replace("-", "_"), self._attrs.get(name))

    def get_dom_attribute(self, name: str):
        return self._attrs.get(name)

    def get_property(self, name: str):
        if name == "value":
            return self.selected_value or (self.typed[-1] if self.typed else self.text)
        return None

    def is_displayed(self) -> bool:
        return self._displayed

    def is_enabled(self) -> bool:
        return True

    def click(self) -> None:
        self.clicked = True

    def clear(self) -> None:
        self.cleared = True

    def send_keys(self, value) -> None:
        self.typed.append(str(value))

    def select_option(self, value: str) -> None:
        self.selected_value = value


class FakeOption:
    def __init__(
        self,
        text: str,
        value: str | None = None,
        selected: bool = False,
        disabled: bool = False,
    ):
        self.tag_name = "option"
        self.text = text
        self._value = value if value is not None else text
        self._selected = selected
        self._disabled = disabled
        self.clicked = False

    def is_selected(self) -> bool:
        return self._selected

    def is_enabled(self) -> bool:
        return not self._disabled

    def is_displayed(self) -> bool:
        return True

    def get_attribute(self, name: str):
        if name == "value":
            return self._value
        if name == "disabled":
            return "true" if self._disabled else None
        return None

    def get_dom_attribute(self, name: str):
        if name == "value":
            return self._value
        if name == "disabled":
            return "true" if self._disabled else None
        return None

    def value_of_css_property(self, prop: str) -> str:
        return "visible"

    def click(self) -> None:
        if not self._disabled:
            self.clicked = True
            self._selected = True


class FakeSelectElement(FakeElement):
    def __init__(
        self,
        name: str = "job_category",
        options: list[FakeOption] | None = None,
        disabled: bool = False,
    ):
        attrs = {"name": name}
        if disabled:
            attrs["disabled"] = "true"
        super().__init__("select", **attrs)
        self.options = options or [
            FakeOption("Select category", value=""),
            FakeOption("Engineering", value="eng"),
            FakeOption("Design", value="des"),
            FakeOption("Finance", value="fin"),
        ]
        self._disabled = disabled

    def get_attribute(self, name: str):
        if name == "name":
            return self._attrs.get("name")
        return super().get_attribute(name)

    def get_dom_attribute(self, name: str):
        if name == "multiple":
            return None
        if name == "name":
            return self._attrs.get("name")
        return super().get_dom_attribute(name)

    def is_enabled(self) -> bool:
        return not self._disabled and "disabled" not in self._attrs

    @property
    def text(self) -> str:
        return " ".join(opt.text for opt in self.options)

    @text.setter
    def text(self, value: str) -> None:
        pass

    def find_elements(self, by, selector):
        return self.options

    def select_option(self, value: str) -> None:
        self.selected_value = value
        for opt in self.options:
            if opt.text == value or opt._value == value:
                if hasattr(opt, "is_enabled") and not opt.is_enabled():
                    raise RuntimeError(f"Cannot select disabled option '{value}'")
                opt._selected = True
            else:
                opt._selected = False


class FakeTab:
    def __init__(
        self,
        handle: str,
        *,
        url: str = "about:blank",
        title: str = "New Tab",
        elements: list | None = None,
        body_text: str = "",
    ):
        self.handle = handle
        self.url = url
        self.title = title
        self.elements = elements or []
        self.body_text = body_text


class FakeSwitchTo:
    def __init__(self, driver: FakeMultiTabDriver):
        self._driver = driver

    def new_window(self, type_hint: str = "tab"):
        return self._driver._create_tab()

    def window(self, handle: str):
        self._driver._switch_to_handle(handle)


class FakeMultiTabDriver:
    def __init__(self, initial_tabs: list[FakeTab] | None = None):
        self._tabs: dict[str, FakeTab] = {}
        self._counter = 0
        if initial_tabs:
            for tab in initial_tabs:
                self._tabs[tab.handle] = tab
            self._current_handle = initial_tabs[0].handle
            self._counter = len(initial_tabs)
        else:
            self._counter = 1
            h = f"handle-{self._counter}"
            self._tabs[h] = FakeTab(
                h, title="Form Page", url="http://localhost:8000/form"
            )
            self._current_handle = h

        self.switch_to = FakeSwitchTo(self)
        self.quit_called = False
        self.scripts: list[str] = []

    @property
    def window_handles(self) -> list[str]:
        return list(self._tabs.keys())

    @property
    def current_window_handle(self) -> str:
        if self._current_handle not in self._tabs:
            raise RuntimeError("NoSuchWindowException: target window already closed")
        return self._current_handle

    @property
    def current_tab(self) -> FakeTab:
        if self._current_handle not in self._tabs:
            raise RuntimeError("NoSuchWindowException: target window already closed")
        return self._tabs[self._current_handle]

    @property
    def title(self) -> str:
        return self.current_tab.title

    @property
    def current_url(self) -> str:
        return self.current_tab.url

    def get(self, url: str):
        self.current_tab.url = url

    def find_elements(self, by, selector):
        return list(self.current_tab.elements)

    def find_element(self, by, selector):
        return FakeElement("body", text=self.current_tab.body_text)

    def close(self):
        if self._current_handle in self._tabs:
            del self._tabs[self._current_handle]

    def quit(self):
        self.quit_called = True
        self._tabs.clear()

    def _create_tab(self):
        self._counter += 1
        h = f"handle-{self._counter}"
        tab = FakeTab(h, title="New Tab", url="about:blank")
        self._tabs[h] = tab
        self._current_handle = h
        return self

    def _switch_to_handle(self, handle: str):
        if handle not in self._tabs:
            raise RuntimeError(f"NoSuchWindowException: Window {handle} not found")
        self._current_handle = handle

    def execute_script(self, script, *args):
        self.scripts.append(script)
        return None

    def set_page_load_timeout(self, timeout):
        pass

    def implicitly_wait(self, timeout):
        pass


def _controller(driver: FakeMultiTabDriver) -> BrowserController:
    controller = BrowserController(headless=True)
    controller._driver = driver
    return controller


# ---------------------------------------------------------------------------
# Unit Tests
# ---------------------------------------------------------------------------


def test_new_tab_opens_fresh_tab_and_switches_context():
    driver = FakeMultiTabDriver()
    controller = _controller(driver)

    result = controller.new_tab()
    assert len(driver.window_handles) == 2
    assert driver.current_window_handle == "handle-2"
    assert "Opened tab 2" in result
    assert controller._elements == []


def test_new_tab_navigates_to_url_when_provided():
    driver = FakeMultiTabDriver()
    controller = _controller(driver)

    result = controller.new_tab("https://example.com/profile")
    assert len(driver.window_handles) == 2
    assert driver.current_url == "https://example.com/profile"
    assert "Opened tab 2" in result


def test_new_tab_resets_element_cache():
    el = FakeElement("a", text="Link")
    driver = FakeMultiTabDriver([FakeTab("handle-1", elements=[el])])
    controller = _controller(driver)
    controller.list_elements()
    assert len(controller._elements) == 1

    controller.new_tab()
    assert controller._elements == []


def test_switch_tab_by_1based_integer_index():
    tab1 = FakeTab("h1", title="Form", url="http://localhost/form")
    tab2 = FakeTab("h2", title="Profile", url="http://localhost/profile")
    driver = FakeMultiTabDriver([tab1, tab2])
    controller = _controller(driver)

    assert driver.current_window_handle == "h1"

    controller.switch_tab(2)
    assert driver.current_window_handle == "h2"
    assert "Profile" in controller.describe_page()

    controller.switch_tab(1)
    assert driver.current_window_handle == "h1"
    assert "Form" in controller.describe_page()


def test_switch_tab_by_handle_string():
    tab1 = FakeTab("h1", title="Form")
    tab2 = FakeTab("h2", title="Profile")
    driver = FakeMultiTabDriver([tab1, tab2])
    controller = _controller(driver)

    controller.switch_tab("h2")
    assert driver.current_window_handle == "h2"


def test_switch_tab_by_digit_string():
    tab1 = FakeTab("h1", title="Form")
    tab2 = FakeTab("h2", title="Profile")
    driver = FakeMultiTabDriver([tab1, tab2])
    controller = _controller(driver)

    controller.switch_tab("2")
    assert driver.current_window_handle == "h2"


def test_switch_tab_out_of_bounds_raises_index_error():
    driver = FakeMultiTabDriver()
    controller = _controller(driver)

    with pytest.raises(IndexError) as exc:
        controller.switch_tab(5)
    assert "Tab 5 does not exist" in str(exc.value)


def test_switch_tab_invalid_type_raises_type_error():
    driver = FakeMultiTabDriver()
    controller = _controller(driver)

    with pytest.raises(TypeError):
        controller.switch_tab([1])  # type: ignore


def test_switch_tab_invalid_string_raises_value_error():
    driver = FakeMultiTabDriver()
    controller = _controller(driver)

    with pytest.raises(ValueError) as exc:
        controller.switch_tab("invalid_handle")
    assert "Tab 'invalid_handle' was not found" in str(exc.value)


def test_switch_tab_resets_element_cache():
    el = FakeElement("button", text="Submit")
    driver = FakeMultiTabDriver([FakeTab("h1", elements=[el]), FakeTab("h2")])
    controller = _controller(driver)
    controller.list_elements()
    assert len(controller._elements) == 1

    controller.switch_tab(2)
    assert controller._elements == []


def test_list_tabs_enumerates_all_tabs_and_restores_active_focus():
    tab1 = FakeTab("h1", title="Form", url="http://test/form")
    tab2 = FakeTab("h2", title="Profile", url="http://test/profile")
    driver = FakeMultiTabDriver([tab1, tab2])
    controller = _controller(driver)

    tabs = controller.list_tabs()
    assert len(tabs) == 2
    assert tabs[0] == {
        "index": 1,
        "title": "Form",
        "url": "http://test/form",
        "is_active": True,
        "handle": "h1",
    }
    assert tabs[1] == {
        "index": 2,
        "title": "Profile",
        "url": "http://test/profile",
        "is_active": False,
        "handle": "h2",
    }
    # Verify active tab remained h1
    assert driver.current_window_handle == "h1"


def test_list_tabs_when_browser_not_open_returns_empty():
    controller = BrowserController(headless=True)
    assert controller.list_tabs() == []


def test_close_tab_active_switches_to_adjacent_tab():
    tab1 = FakeTab("h1", title="Form")
    tab2 = FakeTab("h2", title="Profile")
    driver = FakeMultiTabDriver([tab1, tab2])
    controller = _controller(driver)

    controller.switch_tab(2)
    assert driver.current_window_handle == "h2"

    result = controller.close_tab()
    assert "Closed tab 2" in result
    assert driver.window_handles == ["h1"]
    assert driver.current_window_handle == "h1"
    assert controller._elements == []


def test_close_tab_background_preserves_active_focus():
    tab1 = FakeTab("h1", title="Form")
    tab2 = FakeTab("h2", title="Profile")
    driver = FakeMultiTabDriver([tab1, tab2])
    controller = _controller(driver)

    result = controller.close_tab(2)
    assert "Closed tab 2" in result
    assert driver.window_handles == ["h1"]
    assert driver.current_window_handle == "h1"


def test_close_tab_last_remaining_closes_browser():
    driver = FakeMultiTabDriver([FakeTab("h1")])
    controller = _controller(driver)

    result = controller.close_tab(1)
    assert "Browser closed" in result
    assert controller.is_open is False


def test_close_tab_when_browser_not_open_returns_message():
    controller = BrowserController(headless=True)
    assert controller.close_tab() == "The browser was not open."


def test_close_tab_invalid_index_raises_index_error():
    driver = FakeMultiTabDriver([FakeTab("h1")])
    controller = _controller(driver)

    with pytest.raises(IndexError):
        controller.close_tab(4)


def test_validate_value_field_allows_date_inputs():
    date_el = FakeElement("input", type="date")
    BrowserController._validate_value_field(date_el, writable=True)

    dt_el = FakeElement("input", type="datetime-local")
    BrowserController._validate_value_field(dt_el, writable=True)

    select_el = FakeSelectElement()
    BrowserController._validate_value_field(select_el, writable=False)


def test_type_text_into_date_input():
    date_el = FakeElement("input", type="date", name="dob")
    driver = FakeMultiTabDriver([FakeTab("h1", elements=[date_el])])
    controller = _controller(driver)
    controller.list_elements()

    result = controller.type_text(1, "1995-05-15")
    assert date_el.cleared is True
    assert "1995-05-15" in date_el.typed
    assert "Typed into the textbox" in result


def test_type_text_into_select_raises_value_error():
    sel_el = FakeSelectElement()
    driver = FakeMultiTabDriver([FakeTab("h1", elements=[sel_el])])
    controller = _controller(driver)
    controller.list_elements()

    with pytest.raises(ValueError) as exc:
        controller.type_text(1, "anything")
    assert "is a select dropdown, not a text field" in str(exc.value)


def test_select_dropdown_by_visible_text():
    sel_el = FakeSelectElement()
    driver = FakeMultiTabDriver([FakeTab("h1", elements=[sel_el])])
    controller = _controller(driver)
    controller.list_elements()

    result = controller.select_dropdown(1, "Engineering")
    assert "Selected 'Engineering'" in result
    assert controller._elements == []


def test_select_dropdown_by_value():
    sel_el = FakeSelectElement()
    driver = FakeMultiTabDriver([FakeTab("h1", elements=[sel_el])])
    controller = _controller(driver)
    controller.list_elements()

    result = controller.select_dropdown(1, "eng")
    assert "Selected 'eng'" in result


def test_select_dropdown_case_insensitive():
    sel_el = FakeSelectElement()
    driver = FakeMultiTabDriver([FakeTab("h1", elements=[sel_el])])
    controller = _controller(driver)
    controller.list_elements()

    result = controller.select_dropdown(1, "engineering")
    assert "Selected 'Engineering'" in result or "Selected 'engineering'" in result


def test_select_dropdown_non_select_raises_value_error():
    textbox = FakeElement("input", type="text", name="first_name")
    driver = FakeMultiTabDriver([FakeTab("h1", elements=[textbox])])
    controller = _controller(driver)
    controller.list_elements()

    with pytest.raises(ValueError) as exc:
        controller.select_dropdown(1, "option_a")
    assert "not a <select> dropdown" in str(exc.value)


def test_select_dropdown_not_found_raises_value_error_with_options():
    sel_el = FakeSelectElement()
    driver = FakeMultiTabDriver([FakeTab("h1", elements=[sel_el])])
    controller = _controller(driver)
    controller.list_elements()

    with pytest.raises(ValueError) as exc:
        controller.select_dropdown(1, "NonExistentOption")
    assert "not found in dropdown" in str(exc.value)
    assert "Available options" in str(exc.value)


def test_select_dropdown_disabled_raises_value_error():
    sel_el = FakeSelectElement()
    sel_el._attrs["disabled"] = "true"
    driver = FakeMultiTabDriver([FakeTab("h1", elements=[sel_el])])
    controller = _controller(driver)
    controller.list_elements()

    with pytest.raises(ValueError) as exc:
        controller.select_dropdown(1, "Engineering")
    assert "disabled dropdown" in str(exc.value)


def test_fake_option_disabled_state_and_dom_attributes():
    """Verify FakeOption correctly models enabled and disabled DOM properties."""
    enabled_opt = FakeOption("Active", value="act", disabled=False)
    assert enabled_opt.is_enabled() is True
    assert enabled_opt.get_dom_attribute("disabled") is None
    assert enabled_opt.get_attribute("disabled") is None
    enabled_opt.click()
    assert enabled_opt.clicked is True
    assert enabled_opt.is_selected() is True

    disabled_opt = FakeOption("Inactive", value="inact", disabled=True)
    assert disabled_opt.is_enabled() is False
    assert disabled_opt.get_dom_attribute("disabled") == "true"
    assert disabled_opt.get_attribute("disabled") == "true"
    disabled_opt.click()
    assert disabled_opt.clicked is False
    assert disabled_opt.is_selected() is False


def test_select_dropdown_disabled_option_by_label_raises_value_error():
    """Verify attempting to select a disabled option by label/text raises ValueError."""
    options = [
        FakeOption("Select category", value=""),
        FakeOption("Engineering", value="eng"),
        FakeOption("Architecture (Deprecated)", value="arch", disabled=True),
        FakeOption("Design", value="des"),
    ]
    sel_el = FakeSelectElement(options=options)
    driver = FakeMultiTabDriver([FakeTab("h1", elements=[sel_el])])
    controller = _controller(driver)
    controller.list_elements()

    with pytest.raises(ValueError) as exc:
        controller.select_dropdown(1, "Architecture (Deprecated)")
    assert "Cannot select disabled option" in str(exc.value)
    assert "Architecture (Deprecated)" in str(exc.value)


def test_select_dropdown_disabled_option_by_value_raises_value_error():
    """Verify attempting to select a disabled option by value raises ValueError."""
    options = [
        FakeOption("Select category", value=""),
        FakeOption("Engineering", value="eng"),
        FakeOption("Architecture (Deprecated)", value="arch", disabled=True),
        FakeOption("Design", value="des"),
    ]
    sel_el = FakeSelectElement(options=options)
    driver = FakeMultiTabDriver([FakeTab("h1", elements=[sel_el])])
    controller = _controller(driver)
    controller.list_elements()

    with pytest.raises(ValueError) as exc:
        controller.select_dropdown(1, "arch")
    assert "Cannot select disabled option" in str(exc.value)
    assert "arch" in str(exc.value)


def test_select_dropdown_disabled_option_preserves_enabled_selection():
    """Verify selecting enabled options works alongside disabled options without interference."""
    options = [
        FakeOption("Select category", value=""),
        FakeOption("Engineering", value="eng"),
        FakeOption("Architecture (Deprecated)", value="arch", disabled=True),
        FakeOption("Design", value="des"),
    ]
    sel_el = FakeSelectElement(options=options)
    driver = FakeMultiTabDriver([FakeTab("h1", elements=[sel_el])])
    controller = _controller(driver)
    controller.list_elements()

    res = controller.select_dropdown(1, "Engineering")
    assert "Selected 'Engineering'" in res
    assert sel_el.selected_value == "eng"


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
