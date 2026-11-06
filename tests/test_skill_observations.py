from unittest.mock import Mock, patch

import pytest

from browser import BrowserController
from desktop import DesktopController


def test_page_checks_use_current_dom_and_do_not_start_a_browser():
    driver = Mock()
    driver.find_element.return_value.text = "Welcome to Your Library"
    driver.find_elements.return_value = [Mock(is_displayed=lambda: True)]
    browser = BrowserController()
    browser._driver = driver
    assert browser.observe("page_contains", "Your Library")
    assert not browser.observe("page_contains", "Sign in")
    assert browser.observe("page_element", 'button[aria-label="Pause"]')
    driver.find_elements.return_value = [Mock(is_displayed=lambda: False)]
    assert not browser.observe("page_element", 'button[aria-label="Pause"]')
    with pytest.raises(ValueError):
        browser.observe("looks_like", "probably finished")


def test_window_check_reads_observed_window_titles():
    desktop = DesktopController(mouse=Mock(getAllTitles=lambda: ["Notepad", "IRA"]))
    assert desktop.window_titles() == ["Notepad", "IRA"]


def test_playlist_click_without_observed_playback_is_a_failure():
    driver = Mock()
    driver.execute_script.side_effect = ["matched", "play-requested"]
    browser = BrowserController()
    browser._driver = driver
    with (
        patch("browser.time.sleep"),
        patch.object(browser, "playback_active", create=True, return_value=False),
        pytest.raises(RuntimeError, match="playback"),
    ):
        browser.spotify_play_playlist("My Funk Songs")


def test_spotify_no_audio_does_not_report_success():
    driver = Mock()
    driver.execute_script.return_value = "no-audio"
    browser = BrowserController()
    browser._driver = driver
    with pytest.raises(RuntimeError, match="audio"):
        browser.spotify_control("play")


def test_playing_audio_from_another_playlist_cannot_verify_requested_playlist():
    driver = Mock()
    driver.execute_script.side_effect = ["matched", "play-requested"]
    browser = BrowserController()
    browser._driver = driver
    with (
        patch("browser.time.sleep"),
        patch.object(browser, "playback_active", return_value=True),
        patch.object(
            browser, "playlist_context_matches", create=True, return_value=False
        ),
        pytest.raises(RuntimeError, match="playback"),
    ):
        browser.spotify_play_playlist("My Funk Songs")


def test_page_read_failure_is_not_successful_prose():
    driver = Mock()
    driver.find_element.side_effect = RuntimeError("page disconnected")
    browser = BrowserController()
    browser._driver = driver
    with pytest.raises(RuntimeError, match="read"):
        browser.read_page()
