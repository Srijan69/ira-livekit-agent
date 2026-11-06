"""Empirical stress tests and adversarial edge-case challenges for multi-tab browser management."""

from __future__ import annotations

import random
from pathlib import Path

import pytest
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

from browser import BrowserController
from tests.test_browser_multitab import (
    FakeElement,
    FakeMultiTabDriver,
    FakeSelectElement,
    FakeTab,
    _controller,
)

# ===========================================================================
# Stress Test 1: Rapid Sequential Opening and Closing of 10+ Tabs
# ===========================================================================


def test_stress_rapid_open_and_close_sequential_tabs():
    """Stress test opening 15 tabs sequentially and closing them rapidly from active tab."""
    driver = FakeMultiTabDriver()
    controller = _controller(driver)

    # 1. Rapidly open 15 tabs with alternating URLs
    for i in range(2, 16):
        url = f"http://localhost:8000/page_{i}" if i % 2 == 0 else ""
        msg = controller.new_tab(url)
        assert f"Opened tab {i}" in msg
        assert len(driver.window_handles) == i
        assert driver.current_window_handle == f"handle-{i}"
        assert controller._elements == []

    # 2. Verify list_tabs reflects exactly 15 tabs, with handle-15 active
    tabs = controller.list_tabs()
    assert len(tabs) == 15
    for idx, tab_info in enumerate(tabs, start=1):
        assert tab_info["index"] == idx
        if idx == 15:
            assert tab_info["is_active"] is True
            assert tab_info["handle"] == "handle-15"
        else:
            assert tab_info["is_active"] is False

    # 3. Rapidly close active tabs sequentially from 15 down to 1
    for count in range(15, 1, -1):
        active_handle = driver.current_window_handle
        active_idx = driver.window_handles.index(active_handle) + 1
        msg = controller.close_tab()
        assert f"Closed tab {active_idx}" in msg
        assert len(driver.window_handles) == count - 1
        # Active tab must switch to an existing open handle
        assert driver.current_window_handle in driver.window_handles
        assert controller._elements == []

    # 4. Closing the last remaining tab must cleanly close the browser
    assert len(driver.window_handles) == 1
    close_msg = controller.close_tab()
    assert "Browser closed as no tabs remain" in close_msg
    assert controller.is_open is False
    assert driver.quit_called is True


def test_stress_rapid_close_background_tabs_preserves_active():
    """Stress test opening 12 tabs and closing background tabs rapidly while focus stays on Tab 1."""
    driver = FakeMultiTabDriver()
    controller = _controller(driver)

    for _ in range(2, 13):
        controller.new_tab()

    assert len(driver.window_handles) == 12

    # Switch back to Tab 1
    controller.switch_tab(1)
    assert driver.current_window_handle == "handle-1"

    # Close background tabs in reverse order: 12 down to 2
    for target in range(12, 1, -1):
        msg = controller.close_tab(target)
        assert f"Closed tab {target}" in msg
        assert driver.current_window_handle == "handle-1"
        assert len(driver.window_handles) == target - 1
        assert controller._elements == []

    # Exactly 1 tab remains
    assert len(driver.window_handles) == 1
    assert driver.current_window_handle == "handle-1"
    tabs = controller.list_tabs()
    assert len(tabs) == 1
    assert tabs[0]["is_active"] is True


# ===========================================================================
# Stress Test 2: Out-of-bounds Tab Indices & Invalid Types
# ===========================================================================


def test_stress_switch_tab_out_of_bounds_and_invalid_types():
    """Exhaustively verify bounds checking and type validation on switch_tab."""
    tab1 = FakeTab("h1", title="Tab 1")
    tab2 = FakeTab("h2", title="Tab 2")
    tab3 = FakeTab("h3", title="Tab 3")
    driver = FakeMultiTabDriver([tab1, tab2, tab3])
    controller = _controller(driver)

    # Integer out-of-bounds
    invalid_ints = [-999, -5, -1, 0, 4, 5, 100]
    for bad_idx in invalid_ints:
        with pytest.raises(IndexError) as exc:
            controller.switch_tab(bad_idx)
        assert f"Tab {bad_idx} does not exist" in str(exc.value)

    # String numeric out-of-bounds
    invalid_digit_strings = ["0", " 0 ", "4", " 5 ", "100", " 100 "]
    for bad_str in invalid_digit_strings:
        with pytest.raises(IndexError) as exc:
            controller.switch_tab(bad_str)
        assert f"Tab {int(bad_str.strip())} does not exist" in str(exc.value)

    # Non-digit strings & negative string representations
    invalid_strings = ["-1", "-5", "abc", "", "   ", "tab-1", "null", "undefined"]
    for bad_str in invalid_strings:
        with pytest.raises(ValueError) as exc:
            controller.switch_tab(bad_str)
        assert f"Tab '{bad_str}' was not found" in str(exc.value)

    # Invalid types: booleans (subclass of int), floats, lists, dicts, None
    invalid_types = [None, True, False, 1.5, [1], {"tab": 1}, (1, 2)]
    for bad_val in invalid_types:
        with pytest.raises(TypeError) as exc:
            controller.switch_tab(bad_val)  # type: ignore
        assert "Tab identifier must be an integer or handle string" in str(exc.value)


def test_stress_close_tab_out_of_bounds_and_invalid_types():
    """Exhaustively verify bounds checking and type validation on close_tab."""
    tab1 = FakeTab("h1", title="Tab 1")
    tab2 = FakeTab("h2", title="Tab 2")
    driver = FakeMultiTabDriver([tab1, tab2])
    controller = _controller(driver)

    # Integer out-of-bounds
    for bad_idx in [-10, -1, 0, 3, 10]:
        with pytest.raises(IndexError):
            controller.close_tab(bad_idx)

    # Numeric strings out-of-bounds
    for bad_str in ["0", " 0 ", "3", " 10 "]:
        with pytest.raises(IndexError):
            controller.close_tab(bad_str)

    # Non-digit strings
    for bad_str in ["-1", "abc", "", "   ", "nonexistent"]:
        with pytest.raises(ValueError):
            controller.close_tab(bad_str)

    # Invalid types
    for bad_val in [True, False, 2.5, [2], {"tab": 2}]:
        with pytest.raises(TypeError):
            controller.close_tab(bad_val)  # type: ignore


# ===========================================================================
# Stress Test 3: Switching to Closed or Non-existent Tab Handles
# ===========================================================================


def test_stress_switching_to_closed_and_nonexistent_handles():
    """Verify handling of stale handles, closed handles, and closed browser states."""
    tab1 = FakeTab("h1", title="Tab 1")
    tab2 = FakeTab("h2", title="Tab 2")
    driver = FakeMultiTabDriver([tab1, tab2])
    controller = _controller(driver)

    # 1. Non-existent arbitrary string handles
    with pytest.raises(ValueError) as exc:
        controller.switch_tab("handle-999-not-real")
    assert "Tab 'handle-999-not-real' was not found" in str(exc.value)

    with pytest.raises(ValueError) as exc:
        controller.close_tab("handle-999-not-real")
    assert "Tab 'handle-999-not-real' was not found" in str(exc.value)

    # 2. Close Tab 2 using handle string
    controller.close_tab("h2")
    assert "h2" not in driver.window_handles

    # 3. Attempting to switch to or close the now-stale handle must fail with ValueError
    with pytest.raises(ValueError) as exc:
        controller.switch_tab("h2")
    assert "Tab 'h2' was not found" in str(exc.value)

    with pytest.raises(ValueError) as exc:
        controller.close_tab("h2")
    assert "Tab 'h2' was not found" in str(exc.value)

    # 4. Close last tab
    controller.close_tab("h1")
    assert controller.is_open is False

    # 5. Calling multi-tab methods after browser is closed
    assert controller.close_tab() == "The browser was not open."
    assert controller.list_tabs() == []

    with pytest.raises(RuntimeError) as exc:
        controller.switch_tab(1)
    assert "No page is open yet" in str(exc.value)


# ===========================================================================
# Stress Test 4: Closing Tabs in Arbitrary / Random Permutations
# ===========================================================================


def test_stress_closing_tabs_arbitrary_permutations():
    """Stress test closing middle, first, and last tabs across 25 randomized permutations."""
    rng = random.Random(42)

    for run_idx in range(25):
        num_tabs = rng.randint(4, 9)
        tabs = [
            FakeTab(f"run{run_idx}-tab-{k}", title=f"Title {k}")
            for k in range(1, num_tabs + 1)
        ]
        driver = FakeMultiTabDriver(tabs)
        controller = _controller(driver)

        while len(driver.window_handles) > 1:
            handles = list(driver.window_handles)
            curr = driver.current_window_handle
            assert curr in handles

            action = rng.choice(
                [
                    "close_active",
                    "close_first",
                    "close_last",
                    "close_middle",
                    "switch_random",
                ]
            )
            if action == "switch_random":
                target_idx = rng.randint(1, len(handles))
                controller.switch_tab(target_idx)
                assert driver.current_window_handle == handles[target_idx - 1]
            elif action == "close_active":
                controller.close_tab()
                assert driver.current_window_handle in driver.window_handles
                assert controller._elements == []
            elif action == "close_first":
                controller.close_tab(1)
                assert driver.current_window_handle in driver.window_handles
                assert controller._elements == []
            elif action == "close_last":
                controller.close_tab(len(handles))
                assert driver.current_window_handle in driver.window_handles
                assert controller._elements == []
            elif action == "close_middle":
                mid_idx = (len(handles) // 2) + 1
                controller.close_tab(mid_idx)
                assert driver.current_window_handle in driver.window_handles
                assert controller._elements == []

            # Check list_tabs consistency invariant
            tab_list = controller.list_tabs()
            assert len(tab_list) == len(driver.window_handles)
            active_tabs = [t for t in tab_list if t["is_active"]]
            assert len(active_tabs) == 1
            assert active_tabs[0]["handle"] == driver.current_window_handle

        # Final remaining tab close
        assert len(driver.window_handles) == 1
        controller.close_tab()
        assert controller.is_open is False


# ===========================================================================
# Stress Test 5: Cache Invalidation & Stale Lookups vs Auto-resolve
# ===========================================================================


def test_stress_element_cache_invalidation_prevents_stale_lookups():
    """Verify element cache lifecycle on tab switches, closures, and navigation.

    Critical architectural observation:
    - switch_tab resets self._elements = [] then invokes describe_page(), which
      re-populates self._elements with elements from the newly focused page.
    - new_tab and close_tab leave self._elements = [] until an explicit list/resolve.
    - Stale elements from previous tab are completely evicted; non-existent indices
      on the target page strictly raise IndexError.
    """
    btn = FakeElement("button", text="Submit Form")
    txt = FakeElement("input", type="text", name="username")
    sel = FakeSelectElement(name="category")

    tab1 = FakeTab("h1", title="Form Page", elements=[btn, txt, sel])
    tab2 = FakeTab("h2", title="Profile Page", elements=[])  # 0 interactive elements
    tab3 = FakeTab("h3", title="Dashboard", elements=[FakeElement("a", text="Home")])

    driver = FakeMultiTabDriver([tab1, tab2, tab3])
    controller = _controller(driver)

    # 1. On Tab 1: list elements and cache them
    controller.list_elements()
    assert len(controller._elements) == 3
    # Lookups on Tab 1 succeed
    controller.click(1)
    assert btn.clicked is True
    controller.type_text(2, "alice")
    assert "alice" in txt.typed

    # 2. Switch to Tab 2: describe_page() re-indexes Tab 2.
    # Since Tab 2 has 0 elements, self._elements is empty [].
    controller.switch_tab(2)
    assert controller._elements == []

    # Tab 2 has 0 elements. Attempting any element operation immediately raises IndexError
    with pytest.raises(IndexError) as exc:
        controller.click(1)
    assert "There is no element 1. The page has 0." in str(exc.value)

    with pytest.raises(IndexError) as exc:
        controller.type_text(2, "stale_text")
    assert "There is no element 2. The page has 0." in str(exc.value)

    with pytest.raises(IndexError) as exc:
        controller.select_dropdown(3, "Engineering")
    assert "There is no element 3. The page has 0." in str(exc.value)

    # 3. Switch to Tab 3 (has 1 element: Home link)
    controller.switch_tab(3)
    # Cache now contains Tab 3's element, NOT Tab 1's or Tab 2's
    assert len(controller._elements) == 1
    # Index 2 or 3 (stale from Tab 1) immediately raises IndexError
    with pytest.raises(IndexError):
        controller.click(2)

    # 4. New tab creation: self._elements is strictly emptied
    controller.new_tab()
    assert controller._elements == []
    # New blank tab has 0 elements, so index 1 raises IndexError
    with pytest.raises(IndexError):
        controller.click(1)

    # 5. Switch back to Tab 1: describe_page() re-indexes Tab 1's 3 elements
    controller.switch_tab(1)
    assert len(controller._elements) == 3

    # 6. Closing background tab 2: self._elements is strictly emptied
    controller.close_tab(2)
    assert controller._elements == []

    # Re-cache Tab 1 elements
    controller.list_elements()
    assert len(controller._elements) == 3

    # 7. Back navigation: describe_page() re-indexes the page
    driver.back = lambda: None
    controller.back()
    assert len(controller._elements) == 3


# ===========================================================================
# Stress Test 6: Live Headless Chrome Multi-Tab Lifecycle & Form Controls
# ===========================================================================


def test_stress_live_headless_chrome_multitab_and_invalidation(tmp_path: Path):
    """Execute live end-to-end multi-tab operations against real headless Chrome.

    Validates actual W3C WebDriver tab creation, navigation, switching, form inputs,
    dropdown selections, and cache invalidation.
    """
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument(f"--user-data-dir={tmp_path / 'chrome_user_data'}")
    options.add_argument("--remote-debugging-port=0")

    driver = webdriver.Chrome(options=options)
    controller = BrowserController(headless=True)
    controller._driver = driver

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
        assert len(driver.window_handles) == 1

        # List elements on Tab 1
        elements_1 = controller.list_elements()
        assert len(elements_1) == 4
        assert len(controller._elements) == 4

        # Type name and date, select dropdown
        controller.type_text(1, "Empirical Challenger")
        controller.type_text(2, "2026-10-19")
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
        assert len(driver.window_handles) == 2
        # Cache MUST be cleared upon new tab creation
        assert controller._elements == []

        # Out-of-bounds on Tab 2 (Tab 2 only has 1 interactive element: portfolio_link)
        with pytest.raises(IndexError) as exc:
            controller.click(5)
        assert "There is no element 5" in str(exc.value)

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
        assert "There is no element 10" in str(exc.value)

        # Click submit button (element 4 on Tab 1)
        controller.click(4)
        assert "Submitted" in driver.title

        # 4. Rapidly open 8 more tabs in real headless Chrome (reaching 10 tabs)
        for i in range(3, 11):
            tab_extra = tmp_path / f"tab_{i}.html"
            tab_extra.write_text(
                f"<!DOCTYPE html><html><body>Tab {i}</body></html>", encoding="utf-8"
            )
            controller.new_tab(tab_extra.as_uri())
            assert len(driver.window_handles) == i

        assert len(controller.list_tabs()) == 10

        # 5. Close tabs across various positions: middle, active, first
        controller.close_tab(5)  # close tab 5 in middle
        assert len(driver.window_handles) == 9

        controller.close_tab()  # close active tab
        assert len(driver.window_handles) == 8

        controller.close_tab(1)  # close first tab
        assert len(driver.window_handles) == 7

        # 6. Close remaining tabs until 1 tab left
        while len(driver.window_handles) > 1:
            controller.close_tab()

        assert len(driver.window_handles) == 1

        # 7. Close final tab closes browser
        msg = controller.close_tab()
        assert "Browser closed" in msg
        assert controller.is_open is False

    finally:
        if controller.is_open:
            controller.close()
