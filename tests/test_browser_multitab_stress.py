"""Stress and adversarial checks for native Page tab lifecycles."""

import os
import random
from pathlib import Path

import pytest

from browser import BrowserController
from tests.fixtures.playwright_fakes import (
    FakeContext,
    FakeElement,
    FakePage,
    FakeSelectElement,
    controller_with,
)


def test_stress_rapid_open_and_close_sequential_tabs():
    browser = controller_with()
    for count in range(2, 16):
        browser.new_tab(f"http://localhost/page_{count}" if count % 2 == 0 else "")
        tabs = browser.list_tabs()
        assert len(tabs) == count
        assert tabs[-1]["is_active"]
        assert len({t["handle"] for t in tabs}) == count
    while len(browser.list_tabs()) > 1:
        count = len(browser.list_tabs())
        browser.close_tab()
        assert len(browser.list_tabs()) == count - 1
        assert sum(t["is_active"] for t in browser.list_tabs()) == 1
    assert "Browser closed" in browser.close_tab()
    assert not browser.is_open
    browser.close()


def test_stress_rapid_close_background_tabs_preserves_active():
    browser = controller_with()
    first = browser.list_tabs()[0]["handle"]
    for _ in range(11):
        browser.new_tab()
    browser.switch_tab(first)
    for index in range(12, 1, -1):
        browser.close_tab(index)
        tabs = browser.list_tabs()
        assert len(tabs) == index - 1
        assert tabs[0]["handle"] == first and tabs[0]["is_active"]
    browser.close()


@pytest.mark.parametrize("method", ["switch_tab", "close_tab"])
def test_stress_invalid_tab_identifiers_preserve_pages(method):
    browser = controller_with(context=FakeContext([FakePage(), FakePage(), FakePage()]))
    initial = browser.list_tabs()
    cases = [
        (-999, IndexError),
        (0, IndexError),
        (4, IndexError),
        (" 100 ", IndexError),
        ("-1", ValueError),
        ("abc", ValueError),
        ("", ValueError),
        ("   ", ValueError),
        ("null", ValueError),
        (True, TypeError),
        (False, TypeError),
        (1.5, TypeError),
        ([1], TypeError),
        ({"tab": 1}, TypeError),
        ((1, 2), TypeError),
    ]
    for identifier, error in cases:
        with pytest.raises(error):
            getattr(browser, method)(identifier)
        assert browser.list_tabs() == initial
    browser.close()


def test_stress_closed_ids_and_unavailable_context():
    browser = controller_with(context=FakeContext([FakePage(), FakePage()]))
    old_id = browser.list_tabs()[1]["handle"]
    browser.close_tab(old_id)
    for method in [browser.switch_tab, browser.close_tab]:
        with pytest.raises(ValueError):
            method(old_id)
    browser.close_tab()
    assert browser.list_tabs() == []
    with pytest.raises(RuntimeError):
        browser.switch_tab(1)
    browser.close()


def test_stress_closing_tabs_arbitrary_permutations():
    rng = random.Random(42)
    for run in range(25):
        browser = controller_with(
            context=FakeContext(
                [FakePage(title=f"{run}: {k}") for k in range(rng.randint(4, 9))]
            )
        )
        while len(browser.list_tabs()) > 1:
            before = browser.list_tabs()
            target = rng.choice(before)["handle"]
            if rng.choice([True, False]):
                browser.switch_tab(target)
            browser.close_tab(target)
            after = browser.list_tabs()
            assert len(after) == len(before) - 1
            assert sum(t["is_active"] for t in after) == 1
            assert target not in {t["handle"] for t in after}
            assert {t["handle"] for t in after} <= {t["handle"] for t in before}
        browser.close_tab()
        assert not browser.is_open
        browser.close()


def test_stress_element_cache_invalidation_prevents_stale_lookups():
    button, text, select = (
        FakeElement("button", text="Submit"),
        FakeElement(),
        FakeSelectElement(),
    )
    browser = controller_with(
        context=FakeContext(
            [
                FakePage([button, text, select]),
                FakePage([]),
                FakePage([FakeElement("a", text="Home")]),
            ]
        )
    )
    browser.list_elements()
    browser.click(1)
    with pytest.raises(IndexError):
        browser.type_text(2, "stale")
    browser.list_elements()
    browser.type_text(2, "alice")
    assert text.input_value() == "alice"
    browser.switch_tab(2)
    for method, args in [
        (browser.click, (1,)),
        (browser.type_text, (2, "stale")),
        (browser.select_dropdown, (3, "eng")),
    ]:
        with pytest.raises(IndexError):
            method(*args)
    browser.switch_tab(3)
    assert len(browser._elements) == 1
    with pytest.raises(IndexError):
        browser.click(2)
    browser.new_tab()
    assert browser._elements == []
    browser.switch_tab(1)
    browser.close_tab(2)
    # Closing a tab describes the active page and supplies fresh controls.
    assert len(browser._elements) == 3
    browser.back()
    assert len(browser._elements) == 3
    browser.close()


@pytest.mark.skipif(
    os.environ.get("IRA_RUN_BROWSER_EVALS") != "1",
    reason="Real Playwright evaluation is opt-in",
)
def test_stress_live_headless_chrome_multitab_and_invalidation(
    tmp_path: Path, monkeypatch
):
    """Execute live end-to-end multi-tab operations against real headless Chrome.

    Validates actual native Playwright tab creation, navigation, switching, form inputs,
    dropdown selections, and cache invalidation.
    """
    monkeypatch.setenv("IRA_BROWSER_PROFILE", str(tmp_path / "profile" / "Default"))
    controller = BrowserController(headless=True, browser="chrome")

    try:
        # 1. Tab 1: Form page with text, date, and select
        tab1_file = tmp_path / "tab1.html"
        tab1_file.write_text(
            "<!DOCTYPE html><html><body>"
            "<h1>Job Application</h1>"
            "<input id='applicant_name' type='text' name='name' placeholder='Full Name'/>"
            "<input id='start_date' type='date' name='start_date'/>"
            "<select id='role' name='job_category'>"
            "  <option value=''>Choose role</option>"
            "  <option value='eng'>Software Engineer</option>"
            "  <option value='des'>Product Designer</option>"
            "</select>"
            "<button id='submit_btn' onclick=\"document.title='Submitted'\">Apply</button>"
            "</body></html>",
            encoding="utf-8",
        )
        controller.navigate(tab1_file.as_uri())
        assert len(controller.list_tabs()) == 1

        # List elements on Tab 1
        elements_1 = controller.list_elements()
        assert len(elements_1) == 4
        assert len(controller._elements) == 4

        # Type name and date, select dropdown
        controller.type_text(1, "Empirical Challenger")
        controller.list_elements()
        controller.type_text(2, "2026-10-19")
        controller.list_elements()
        controller.select_dropdown(3, "Software Engineer")

        # 2. Open Tab 2 with profile data
        tab2_file = tmp_path / "tab2.html"
        tab2_file.write_text(
            "<!DOCTYPE html><html><body>"
            "<h1>Candidate Profile</h1>"
            "<p id='p_name'>Candidate: Ada Lovelace</p>"
            "<a id='portfolio_link' href='#'>Portfolio</a>"
            "</body></html>",
            encoding="utf-8",
        )
        controller.new_tab(tab2_file.as_uri())
        assert len(controller.list_tabs()) == 2
        # Cache MUST be cleared upon new tab creation
        controller.list_elements()

        # Out-of-bounds on Tab 2 (Tab 2 only has 1 interactive element: portfolio_link)
        with pytest.raises(IndexError) as exc:
            controller.click(5)
        assert "no current element reference 5" in str(exc.value)

        # Tab 2 elements
        elements_2 = controller.list_elements()
        assert len(elements_2) == 1
        assert len(controller._elements) == 1

        # 3. Switch back to Tab 1
        controller.switch_tab(1)
        # describe_page() re-indexed Tab 1 (4 elements)
        assert len(controller._elements) == 4

        # Out-of-bounds index on Tab 1 raises IndexError
        with pytest.raises(IndexError) as exc:
            controller.click(10)
        assert "no current element reference 10" in str(exc.value)

        # Click submit button (element 4 on Tab 1)
        controller.click(4)
        assert "Submitted" in controller.evaluate("() => document.title")

        # 4. Rapidly open 8 more tabs in real headless Chrome (reaching 10 tabs)
        for i in range(3, 11):
            tab_extra = tmp_path / f"tab_{i}.html"
            tab_extra.write_text(
                f"<!DOCTYPE html><html><body>Tab {i}</body></html>", encoding="utf-8"
            )
            controller.new_tab(tab_extra.as_uri())
            assert len(controller.list_tabs()) == i

        assert len(controller.list_tabs()) == 10

        # 5. Close tabs across various positions: middle, active, first
        controller.close_tab(5)  # close tab 5 in middle
        assert len(controller.list_tabs()) == 9

        controller.close_tab()  # close active tab
        assert len(controller.list_tabs()) == 8

        controller.close_tab(1)  # close first tab
        assert len(controller.list_tabs()) == 7

        # 6. Close remaining tabs until 1 tab left
        while len(controller.list_tabs()) > 1:
            controller.close_tab()

        assert len(controller.list_tabs()) == 1

        # 7. Close final tab closes browser
        msg = controller.close_tab()
        assert "Browser closed" in msg
        assert controller.is_open is False

    finally:
        if controller.is_open:
            controller.close()
