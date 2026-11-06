import contextlib
import urllib.parse

import pytest
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

from browser import BrowserController


@pytest.fixture(scope="module")
def real_browser():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-infobars")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    driver = webdriver.Chrome(options=options)
    controller = BrowserController(headless=True)
    controller._driver = driver
    yield controller, driver
    with contextlib.suppress(Exception):
        driver.quit()


def test_real_chrome_date_inputs(real_browser):
    controller, driver = real_browser
    html = """
    data:text/html,<html><body>
        <form id="testform">
            <label for="bday">Birthday:</label>
            <input type="date" id="bday" name="bday">
        </form>
    </body></html>
    """
    driver.get(html)
    controller._elements = []
    controller.list_elements()

    # Valid leap year date
    res = controller.type_text(1, "2024-02-29")
    assert "Typed into" in res
    val = driver.execute_script("return document.getElementById('bday').value;")
    assert val == "2024-02-29"

    # Malformed date: HTML5 date inputs reject invalid formats and set value to empty string
    controller.type_text(1, "invalid-date-string")
    val_malformed = driver.execute_script(
        "return document.getElementById('bday').value;"
    )
    assert val_malformed == ""

    # Leap day in non-leap year (2023-02-29 is invalid in HTML5 calendar)
    controller.type_text(1, "2023-02-29")
    val_invalid_leap = driver.execute_script(
        "return document.getElementById('bday').value;"
    )
    assert val_invalid_leap == ""


def test_real_chrome_datetime_and_month(real_browser):
    controller, driver = real_browser
    html = """
    data:text/html,<html><body>
        <form>
            <input type="datetime-local" id="dt" name="dt">
            <input type="month" id="mth" name="mth">
        </form>
    </body></html>
    """
    driver.get(html)
    controller._elements = []
    controller.list_elements()

    # datetime-local
    controller.type_text(1, "2024-06-15T14:30")
    val_dt = driver.execute_script("return document.getElementById('dt').value;")
    assert val_dt == "2024-06-15T14:30"

    # month
    controller.type_text(2, "2024-06")
    val_mth = driver.execute_script("return document.getElementById('mth').value;")
    assert val_mth == "2024-06"


def test_real_chrome_select_dropdown_complex(real_browser):
    controller, driver = real_browser
    html = """<html><body>
        <select id="role_select" name="role">
            <option value="">-- Choose Role --</option>
            <option value="eng_fe">Frontend Engineer</option>
            <option value="eng_be">Frontend Engineer</option>
            <option value="cpp_dev">C++ / C# (.NET) Engineer</option>
            <option value="space">Special & Symbols <> "quotes"</option>
        </select>
    </body></html>"""
    driver.get("data:text/html;charset=utf-8," + urllib.parse.quote(html))
    controller._elements = []
    controller.list_elements()

    # Select by visible text
    controller.select_dropdown(1, "Frontend Engineer")
    val = driver.execute_script("return document.getElementById('role_select').value;")
    assert val == "eng_fe"

    # Select by unique value to reach second identical label
    controller.select_dropdown(1, "eng_be")
    val2 = driver.execute_script("return document.getElementById('role_select').value;")
    assert val2 == "eng_be"

    # Select with special characters
    controller.select_dropdown(1, "C++ / C# (.NET) Engineer")
    val3 = driver.execute_script("return document.getElementById('role_select').value;")
    assert val3 == "cpp_dev"

    # Select case-insensitive
    controller.select_dropdown(1, "FRONTEND ENGINEER")
    val4 = driver.execute_script("return document.getElementById('role_select').value;")
    assert val4 == "eng_fe"


def test_real_chrome_disabled_option_and_disabled_select(real_browser):
    controller, driver = real_browser
    html = """<html><body>
        <select id="active_select" name="active_select">
            <option value="opt1">Option 1</option>
            <option value="opt2" disabled>Disabled Option</option>
        </select>
        <select id="disabled_select" name="disabled_select" disabled>
            <option value="d1">Disabled Select 1</option>
        </select>
    </body></html>"""
    driver.get("data:text/html;charset=utf-8," + urllib.parse.quote(html))
    controller._elements = []
    controller.list_elements()

    # Disabled select element
    with pytest.raises(ValueError) as exc:
        controller.select_dropdown(2, "Disabled Select 1")
    assert "Cannot select option from disabled dropdown" in str(exc.value)

    # Disabled option within enabled select:
    with pytest.raises(ValueError, match="Cannot select disabled option"):
        controller.select_dropdown(1, "Disabled Option")
    val = driver.execute_script(
        "return document.getElementById('active_select').value;"
    )
    # Confirmed empirically: DOM value remained 'opt1'!
    assert val == "opt1"
