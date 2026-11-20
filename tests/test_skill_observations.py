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


def playlist_browser():
    link = FakeElement(
        "a", text="My Funk Songs", href="https://open.spotify.com/playlist/funk"
    )
    play = FakeElement("button", aria_label="Play My Funk Songs")
    page = FakePage([link, play])
    page.selector_map = {'a[href*="/playlist/"]': [link], "main button": [play]}
    link.on_click = lambda: setattr(
        page, "url", "https://open.spotify.com/playlist/funk"
    )
    return controller_with(page), play


def test_playlist_click_without_observed_playback_is_a_failure(monkeypatch):
    browser, play = playlist_browser()
    monkeypatch.setattr(browser, "playback_active", lambda: False)
    with pytest.raises(RuntimeError, match="playback"):
        browser.spotify_play_playlist("My Funk Songs")
    assert play.actions == ["click"]
    browser.close()


def test_spotify_no_audio_does_not_report_success():
    browser = controller_with()
    with pytest.raises(RuntimeError, match="audio"):
        browser.spotify_control("play")
    browser.close()


def test_playing_audio_from_another_playlist_cannot_verify_requested_playlist(
    monkeypatch,
):
    browser, play = playlist_browser()
    monkeypatch.setattr(browser, "playback_active", lambda: True)
    monkeypatch.setattr(browser, "playlist_context_matches", lambda: False)
    with pytest.raises(RuntimeError, match="playback"):
        browser.spotify_play_playlist("My Funk Songs")
    assert play.actions == ["click"]
    browser.close()


def test_page_read_failure_is_not_successful_prose(monkeypatch):
    page = FakePage()
    browser = controller_with(page)

    def fail():
        raise RuntimeError("page disconnected")

    page.on_find = fail
    with pytest.raises(RuntimeError, match="read"):
        browser.read_page()
    browser.close()
