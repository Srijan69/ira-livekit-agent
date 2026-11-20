from unittest.mock import Mock

import pytest

from desktop import DesktopController
from tests.fixtures.playwright_fakes import FakeElement, FakePage, controller_with


def test_page_checks_use_current_dom_and_do_not_start_a_browser():
    element = FakeElement("button", text="Pause")
    page = FakePage([element], body_text="Welcome to Your Library")
    browser = controller_with(page)
    assert browser.observe("page_contains", "Your Library")
    assert not browser.observe("page_contains", "Sign in")
    assert browser.observe("page_element", 'button[aria-label="Pause"]')
    element._displayed = False
    assert not browser.observe("page_element", 'button[aria-label="Pause"]')
    with pytest.raises(ValueError):
        browser.observe("looks_like", "probably finished")
    assert not page.visited
    browser.close()


def test_window_check_reads_observed_window_titles():
    desktop = DesktopController(mouse=Mock(getAllTitles=lambda: ["Notepad", "IRA"]))
    assert desktop.window_titles() == ["Notepad", "IRA"]


def test_page_read_failure_is_not_successful_prose(monkeypatch):
    page = FakePage()
    browser = controller_with(page)

    def fail():
        raise RuntimeError("page disconnected")

    page.on_find = fail
    with pytest.raises(RuntimeError, match="read"):
        browser.read_page()
    browser.close()
