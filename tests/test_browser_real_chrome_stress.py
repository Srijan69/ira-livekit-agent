"""Real isolated Chrome checks for date, dropdown, and disabled controls."""

import os

import pytest

from browser import BrowserController
from tests.test_playwright_browser_eval import load

pytestmark = pytest.mark.skipif(
    os.environ.get("IRA_RUN_BROWSER_EVALS") != "1",
    reason="Real Playwright evaluation is opt-in",
)


@pytest.fixture
def real_browser(tmp_path, monkeypatch):
    monkeypatch.setenv("IRA_BROWSER_PROFILE", str(tmp_path / "profile" / "Default"))
    browser = BrowserController(headless=True, browser="chrome")
    try:
        yield browser
    finally:
        browser.close()


def test_real_chrome_date_inputs(real_browser):
    load(
        real_browser, '<label for="bday">Birthday</label><input type="date" id="bday">'
    )
    real_browser.list_elements()
    real_browser.type_text(1, "2024-02-29")
    assert (
        real_browser.evaluate("() => document.querySelector('#bday').value")
        == "2024-02-29"
    )
    for invalid in ["invalid-date-string", "2023-02-29"]:
        real_browser.list_elements()
        with pytest.raises(RuntimeError, match="verified"):
            real_browser.type_text(1, invalid)
        assert (
            real_browser.evaluate("() => document.querySelector('#bday').value")
            == "2024-02-29"
        )


def test_real_chrome_datetime_and_month(real_browser):
    load(
        real_browser,
        '<input type="datetime-local" id="dt"><input type="month" id="month">',
    )
    for index, selector, value in [
        (1, "#dt", "2024-06-15T14:30"),
        (2, "#month", "2024-06"),
    ]:
        real_browser.list_elements()
        real_browser.type_text(index, value)
        assert (
            real_browser.evaluate(
                "selector => document.querySelector(selector).value", selector
            )
            == value
        )


def test_real_chrome_select_dropdown_complex(real_browser):
    load(
        real_browser,
        '<select id="role"><option value="">Choose role</option><option value="eng_fe">Frontend Engineer</option><option value="eng_be">Frontend Engineer</option><option value="cpp">C++ / C# (.NET)</option></select>',
    )
    real_browser.list_elements()
    for query in ["Frontend Engineer", "FRONTEND ENGINEER"]:
        with pytest.raises(ValueError, match="ambiguous"):
            real_browser.select_dropdown(1, query)
        assert (
            real_browser.evaluate("() => document.querySelector('#role').value") == ""
        )
    for query, value in [("eng_be", "eng_be"), ("C++ / C# (.NET)", "cpp")]:
        real_browser.list_elements()
        real_browser.select_dropdown(1, query)
        assert (
            real_browser.evaluate("() => document.querySelector('#role').value")
            == value
        )


def test_real_chrome_disabled_option_and_disabled_select(real_browser):
    load(
        real_browser,
        '<select id="active"><option value="one">Option 1</option><option value="two" disabled>Disabled option</option></select><select disabled><option>Disabled select</option></select>',
    )
    real_browser.list_elements()
    with pytest.raises(ValueError, match="disabled dropdown"):
        real_browser.select_dropdown(2, "Disabled select")
    with pytest.raises(ValueError, match="disabled option"):
        real_browser.select_dropdown(1, "Disabled option")
    assert (
        real_browser.evaluate("() => document.querySelector('#active').value") == "one"
    )
