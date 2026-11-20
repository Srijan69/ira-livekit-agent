"""Browser controls and fresh target evidence through native page boundaries."""

import pytest

from browser import (
    MAX_ELEMENTS,
    MAX_PAGE_TEXT,
    BrowserController,
    PageElement,
    _describe,
    browser_config,
)
from tests.fixtures.playwright_fakes import FakeElement, FakePage, controller_with

_controller = controller_with


def test_numbered_click_rechecks_identity_after_native_actionability_wait():
    element = FakeElement("button", text="Save")
    page = FakePage([element])
    browser = controller_with(page)
    browser.list_elements()
    element.on_trial = lambda: setattr(element, "text", "Delete")
    with pytest.raises(RuntimeError, match="changed"):
        browser.click(1)
    assert element.actions == []
    browser.close()


@pytest.mark.parametrize("change", ["url", "replacement", "ambiguity"])
def test_selector_click_rechecks_scope_and_node_after_actionability_wait(change):
    element = FakeElement("button", text="Save")
    page = FakePage([element], url="https://example.test/form")
    browser = controller_with(page)

    def drift():
        if change == "url":
            page.url = "https://example.test/other"
        elif change == "replacement":
            page.elements = [FakeElement("button", text="Save")]
        else:
            page.elements.append(FakeElement("button", text="Save"))

    element.on_trial = drift
    with pytest.raises(RuntimeError, match=r"changed|covered"):
        browser.click_selector("#save", "https://example.test/form")
    assert element.actions == []
    assert all(not item.actions for item in page.elements)
    browser.close()


def test_describe_reads_roles_the_way_a_person_would_say_them():
    assert _describe(FakeElement("a", text="Sign in", href="/login"))[0] == "link"
    assert _describe(FakeElement("button", text="Submit"))[0] == "button"
    assert _describe(FakeElement("input", type="text"))[0] == "textbox"
    assert _describe(FakeElement("textarea"))[0] == "textbox"
    assert _describe(FakeElement("input", type="submit", value="Go"))[0] == "button"
    assert _describe(FakeElement("input", type="checkbox"))[0] == "checkbox"


def test_describe_falls_back_through_label_sources():
    assert _describe(FakeElement("a", text="Docs"))[1] == "Docs"
    assert _describe(FakeElement("a", **{"aria-label": "Home"}))[1] == "Home"
    assert _describe(FakeElement("input", placeholder="Search"))[1] == "Search"
    assert _describe(FakeElement("button")) == ("button", "")


def test_element_labels_are_capped():
    assert len(_describe(FakeElement("a", text="x" * 500))[1]) <= 80


def test_spoken_form_is_numbered():
    assert PageElement(3, "link", "Sign in").spoken() == "3. link Sign in"
    assert PageElement(1, "button", "").spoken() == "1. button"


def test_list_elements_numbers_from_one_and_skips_hidden():
    found = _controller(
        FakePage(
            [
                FakeElement("a", text="First"),
                FakeElement("a", text="Hidden", displayed=False),
                FakeElement("button", text="Second"),
            ]
        )
    ).list_elements()
    assert [e.index for e in found] == [1, 2]
    assert [e.label for e in found] == ["First", "Second"]


def test_list_elements_is_capped():
    assert (
        len(
            _controller(
                FakePage([FakeElement("a", text=f"L{i}") for i in range(200)])
            ).list_elements()
        )
        == MAX_ELEMENTS
    )


def test_stale_elements_are_skipped_not_fatal():
    stale = FakeElement("a")
    stale.stale = True
    found = _controller(
        FakePage([FakeElement("a", text="Good"), stale, FakeElement("a", text="Also")])
    ).list_elements()
    assert [e.label for e in found] == ["Good", "Also"]


def test_click_acts_on_the_numbered_element():
    first, second = FakeElement("a", text="First"), FakeElement("a", text="Second")
    controller = _controller(FakePage([first, second]))
    controller.list_elements()
    assert "Second" in controller.click(2)
    assert second.clicked and not first.clicked


def test_intercepted_click_requests_observation_instead_of_forcing_javascript():
    element = FakeElement("button", text="Accept")
    element.on_trial = lambda: (_ for _ in ()).throw(RuntimeError("covered"))
    page = FakePage([element])
    controller = _controller(page)
    controller.list_elements()
    with pytest.raises(RuntimeError, match="covered"):
        controller.click(1)
    assert not element.clicked
    assert page.scripts == []


def test_click_out_of_range_explains_itself():
    controller = _controller(FakePage([FakeElement("a", text="Only")]))
    controller.list_elements()
    with pytest.raises(IndexError, match="7") as error:
        controller.click(7)
    assert "1" in str(error.value)


def test_click_invalidates_the_numbering():
    controller = _controller(FakePage([FakeElement("a", text="Go")]))
    controller.list_elements()
    controller.click(1)
    assert controller._elements == []
    with pytest.raises(IndexError, match="list_page_elements"):
        controller.click(1)


def test_type_text_fills_and_can_submit():
    box = FakeElement("input", type="text", placeholder="Search")
    controller = _controller(FakePage([box]))
    controller.list_elements()
    controller.type_text(1, "livekit agents")
    assert box.input_value() == "livekit agents"
    controller.list_elements()
    controller.type_text(1, "again", submit=True)
    assert box.actions == [
        ("fill", "livekit agents"),
        ("fill", "again"),
        ("press", "Enter"),
    ]


def test_navigate_assumes_https_for_spoken_addresses():
    page = FakePage()
    _controller(page).navigate("example.com")
    assert page.visited == ["https://example.com"]


def test_navigate_keeps_an_explicit_scheme():
    page = FakePage()
    _controller(page).navigate("http://localhost:8080")
    assert page.visited == ["http://localhost:8080"]


def test_navigate_rejects_an_empty_address():
    page = FakePage()
    with pytest.raises(ValueError, match="No address"):
        _controller(page).navigate("   ")
    assert page.visited == []


def test_read_page_is_capped():
    text = _controller(FakePage(body_text="word " * 5000)).read_page()
    assert len(text) <= MAX_PAGE_TEXT + 20
    assert text.endswith("(truncated)")


def test_describe_page_reports_where_we_are():
    assert (
        "Hacker News"
        in _controller(
            FakePage([FakeElement("a", text="One")], title="Hacker News")
        ).describe_page()
    )


def test_close_is_safe_when_nothing_is_open():
    assert "not open" in BrowserController().close()


def test_close_closes_context_and_forgets_page():
    page = FakePage()
    controller = _controller(page)
    controller.close()
    assert page.closed
    assert controller.is_open is False


def test_browser_config_uses_an_unlocked_persistent_ira_profile(monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\boss\AppData\Local")
    config = browser_config("chrome")
    assert config["browser"] == "chrome"
    assert config["user_data_dir"].endswith(r"IRA\Browser\Chrome")
    assert config["profile_directory"] == "Default"


def test_browser_config_supports_brave_and_an_explicit_profile(monkeypatch, tmp_path):
    monkeypatch.setenv("IRA_BROWSER_PROFILE", str(tmp_path / "Profile 2"))
    config = browser_config("brave")
    assert config["browser"] == "brave"
    assert config["user_data_dir"] == str(tmp_path)
    assert config["profile_directory"] == "Profile 2"


def test_browser_config_does_not_use_the_live_chrome_profile(monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\boss\AppData\Local")
    assert "Google\\Chrome\\User Data" not in browser_config("chrome")["user_data_dir"]


@pytest.mark.parametrize("change", ["password", "name", "readonly", "disabled"])
def test_numbered_fill_rechecks_control_after_editable_wait(change):
    field = FakeElement("input", name="Search")
    browser = controller_with(FakePage([field]))
    browser.list_elements()

    def wait(state, **kwargs):
        if change == "password":
            field._attrs["type"] = "password"
        elif change == "name":
            field._attrs["name"] = "Account password"
        else:
            field._attrs[change] = ""

    field.wait_for_element_state = wait
    with pytest.raises(RuntimeError, match="verified"):
        browser.type_text(1, "private text")
    assert field.actions == []
    browser.close()


@pytest.mark.parametrize("change", ["url", "replacement", "ambiguity"])
def test_selector_fill_rechecks_exact_scope_after_editable_wait(change):
    field = FakeElement("input", name="Search")
    page = FakePage([field], url="https://example.test/form")
    browser = controller_with(page)

    def wait(state, **kwargs):
        if change == "url":
            page.url = "https://example.test/other"
        elif change == "replacement":
            page.elements = [FakeElement("input", name="Search")]
        else:
            page.elements.append(FakeElement("input", name="Search"))

    field.wait_for_element_state = wait
    with pytest.raises(RuntimeError, match="verified"):
        browser.type_selector("#search", "private text", "https://example.test/form")
    assert field.actions == [("fill", "")]
    assert not any(
        ("fill", "private text") in element.actions for element in page.elements
    )
    browser.close()
