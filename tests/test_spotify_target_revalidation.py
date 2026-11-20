"""Spotify targets must retain their observed identity through native click waits."""

import pytest

from spotify_dom import SELECT_TRACK
from tests.fixtures.playwright_fakes import FakeElement, FakePage, controller_with


class TrackRow(FakeElement):
    def __init__(self, button):
        super().__init__("div")
        self.button = button
        self.metadata = {
            "connected": True,
            "title": "Aaya Sher",
            "artists": ["Anirudh Ravichander"],
            "track_path": "/track/requested",
        }

    def evaluate(self, expression, arg=None):
        if "artists:" in expression:
            return {**self.metadata, "artists": list(self.metadata["artists"])}
        if "node.contains" in expression:
            return arg is self.button
        return super().evaluate(expression, arg)

    def query_selector_all(self, selector):
        assert selector == "button"
        return [self.button]


def track_fixture():
    button = FakeElement("button", aria_label="Play Aaya Sher")
    row = TrackRow(button)
    page = FakePage(url="https://open.spotify.com/search/Aaya%20Sher/tracks")
    page.selector_map['[data-testid="tracklist-row"]'] = [row]
    selected = {
        "status": "ready",
        "title": "Aaya Sher",
        "artists": ["Anirudh Ravichander"],
        "track_path": "/track/requested",
        "row_index": 0,
    }

    def evaluate(expression, arg):
        assert expression == SELECT_TRACK
        return {**selected, "artists": list(selected["artists"])}

    page.evaluator = evaluate
    return controller_with(page), page, row, button


@pytest.mark.parametrize("changed", ["title", "artists", "track_path", "page"])
def test_track_changes_during_actionability_wait_send_no_play_click(changed):
    controller, page, row, button = track_fixture()

    def drift():
        if changed == "title":
            row.metadata["title"] = "Aaya Sher Remix"
        elif changed == "artists":
            row.metadata["artists"] = ["Different Artist"]
        elif changed == "track_path":
            row.metadata["track_path"] = "/track/replacement"
        else:
            page.url = "https://open.spotify.com/search/Other/tracks"

    button.on_trial = drift
    try:
        with pytest.raises(RuntimeError):
            controller._select_spotify_track_once("Aaya Sher", "Anirudh Ravichander")
        assert button.actions == []
    finally:
        controller.close()


def playlist_fixture(monkeypatch):
    link = FakeElement(
        "a", text="Requested mix", href="https://open.spotify.com/playlist/requested"
    )
    button = FakeElement("button", aria_label="Play Requested mix")
    page = FakePage(url="https://open.spotify.com/search/Requested%20mix/playlists")
    page.selector_map['a[href*="/playlist/"]'] = [link]
    page.selector_map["main button"] = [button]
    link.on_click = lambda: setattr(page, "url", link.get_attribute("href"))
    controller = controller_with(page)
    monkeypatch.setattr(controller, "playback_active", lambda: True)
    monkeypatch.setattr(controller, "playlist_context_matches", lambda: True)

    def unexpected_poll(*args, **kwargs):
        raise AssertionError("A unique observed target should not need another poll")

    monkeypatch.setattr(controller, "_pause_browser_poll", unexpected_poll)
    return controller, page, link, button


@pytest.mark.parametrize("changed", ["href", "name"])
def test_playlist_link_changes_during_wait_send_no_navigation_click(
    monkeypatch, changed
):
    controller, _, link, button = playlist_fixture(monkeypatch)

    def drift():
        if changed == "href":
            link._attrs["href"] = "https://open.spotify.com/playlist/replacement"
        else:
            link.text = "Replacement mix"

    link.on_trial = drift
    try:
        with pytest.raises(RuntimeError):
            controller.spotify_play_playlist("Requested mix")
        assert link.actions == []
        assert button.actions == []
    finally:
        controller.close()


def test_playlist_page_changes_during_play_wait_send_no_play_click(monkeypatch):
    controller, page, link, button = playlist_fixture(monkeypatch)
    button.on_trial = lambda: setattr(
        page, "url", "https://open.spotify.com/playlist/replacement"
    )
    try:
        with pytest.raises(RuntimeError):
            controller.spotify_play_playlist("Requested mix")
        assert link.actions == ["click"]
        assert button.actions == []
    finally:
        controller.close()
