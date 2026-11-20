"""Opt-in real Brave DOM evaluations, using an isolated disposable profile."""

import os
from urllib.parse import quote

import pytest

from browser import BrowserController
from spotify_dom import PLAYER_STATE, SELECT_TRACK

pytestmark = pytest.mark.skipif(
    os.environ.get("IRA_RUN_BROWSER_EVALS") != "1",
    reason="Set IRA_RUN_BROWSER_EVALS=1 to launch isolated headless Brave",
)


@pytest.fixture(scope="module")
def controller(tmp_path_factory):
    directory = tmp_path_factory.mktemp("spotify-dom-profile") / "Default"
    old = os.environ.get("IRA_BROWSER_PROFILE")
    os.environ["IRA_BROWSER_PROFILE"] = str(directory)
    browser = BrowserController(headless=True, browser="brave")
    try:
        browser.navigate("about:blank")
        yield browser
    finally:
        browser.close()
        if old is None:
            os.environ.pop("IRA_BROWSER_PROFILE", None)
        else:
            os.environ["IRA_BROWSER_PROFILE"] = old


def row(title, track, artist="Example Artist"):
    return f'<div data-testid="tracklist-row"><a href="https://open.spotify.com/track/{track}">{title}</a><a href="https://open.spotify.com/artist/{artist}">{artist}</a><button aria-label="Play {title}" onclick="window.played.push(\'{track}\')">Play</button></div>'


def load(controller, body):
    controller.navigate(
        "data:text/html," + quote("<script>window.played=[];</script>" + body)
    )


def test_matches_requested_song_after_wrong_first_result(controller):
    load(controller, row("Different Song", "wrong") + row("Aaya Sher", "right"))
    result = controller._select_spotify_track_once("aaya sher", "")
    assert result["track_path"] == "/track/right"
    assert controller.evaluate("() => window.played") == ["right"]


def animate_row_after_scroll(monkeypatch, *, change_artist=False):
    from playwright.sync_api import ElementHandle

    scroll = ElementHandle.scroll_into_view_if_needed

    def animate(element, **kwargs):
        scroll(element, **kwargs)
        if element.get_attribute("data-testid") == "tracklist-row":
            element.evaluate(
                """(node,changeArtist) => {
                const animation = node.animate(
                    [{transform:'translateX(0px)'},{transform:'translateX(200px)'},{transform:'translateX(0px)'}],
                    {duration:1200}
                );
                if (changeArtist) animation.onfinish = () => {
                    node.querySelector('a[href*="/artist/"]').textContent = 'Different Artist';
                };
            }""",
                change_artist,
            )

    monkeypatch.setattr(ElementHandle, "scroll_into_view_if_needed", animate)


def test_track_hover_waits_for_row_animation_before_trusted_input(
    controller, monkeypatch
):
    load(controller, row("Aaya Sher", "right"))
    animate_row_after_scroll(monkeypatch)
    result = controller._select_spotify_track_once("Aaya Sher", "Example Artist")
    assert result["status"] == "selected"
    assert controller.evaluate("() => window.played") == ["right"]


def test_track_identity_is_rechecked_after_row_animation(controller, monkeypatch):
    load(controller, row("Aaya Sher", "right"))
    animate_row_after_scroll(monkeypatch, change_artist=True)
    with pytest.raises(RuntimeError, match="changed"):
        controller._select_spotify_track_once("Aaya Sher", "Example Artist")
    assert controller.evaluate("() => window.played") == []


def test_play_button_hover_transition_settles_before_actual_click(controller):
    markup = (
        "<style>button {width:240px;transition:transform 1.2s;}"
        "button:hover {transform:translateX(60px);}</style>"
        + row("Aaya Sher", "right").replace(
            "window.played.push('right')", "window.played.push(event.isTrusted)"
        )
    )
    load(controller, markup)
    controller._worker.run(lambda: controller._page.mouse.move(1200, 800))
    result = controller._select_spotify_track_once("Aaya Sher", "Example Artist")
    assert result["status"] == "selected"
    assert controller.evaluate("() => window.played") == [True]


def test_play_button_hover_artist_change_prevents_click(controller):
    markup = row("Aaya Sher", "right").replace(
        "<button aria-label=",
        "<button onmouseenter=\"document.querySelector('a[href*=artist]').textContent='Different Artist'\" aria-label=",
    )
    load(controller, markup)
    controller._worker.run(lambda: controller._page.mouse.move(1200, 800))
    with pytest.raises(RuntimeError, match="changed"):
        controller._select_spotify_track_once("Aaya Sher", "Example Artist")
    assert controller.evaluate("() => window.played") == []


def test_prepared_click_succeeds_with_slow_real_animation_frames(
    controller, monkeypatch
):
    from playwright.sync_api import ElementHandle

    click = ElementHandle.click
    timings = []

    def start_slow_frames_after_trial(element, **kwargs):
        click(element, **kwargs)
        if kwargs.get("trial"):
            element.evaluate("""() => {
                window.slowFrames=true;
                function frameWork() {
                    const until=performance.now()+350;
                    while(performance.now()<until) {}
                    if(window.slowFrames) requestAnimationFrame(frameWork);
                }
                requestAnimationFrame(frameWork);
            }""")
            elapsed = element.evaluate("""async () => {
                const started=performance.now();
                await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
                return performance.now()-started;
            }""")
            timings.append(elapsed)

    monkeypatch.setattr(ElementHandle, "click", start_slow_frames_after_trial)
    markup = row("Aaya Sher", "right").replace(
        "window.played.push('right')", "window.played.push(event.isTrusted)"
    )
    load(controller, markup)
    try:
        result = controller._select_spotify_track_once("Aaya Sher", "Example Artist")
        assert len(timings) == 1 and timings[0] > 500
        assert result["status"] == "selected"
        assert controller.evaluate("() => window.played") == [True]
    finally:
        controller.evaluate("() => {window.slowFrames=false;}")


def test_ambiguous_title_performs_no_click(controller):
    load(
        controller,
        row("Aaya Sher", "one", "Artist One") + row("Aaya Sher", "two", "Artist Two"),
    )
    result = controller._select_spotify_track_once("Aaya Sher", "")
    assert result["status"] == "ambiguous"
    assert controller.evaluate("() => window.played") == []


def test_duplicate_releases_by_same_artists_are_one_song(controller):
    load(controller, row("Aaya Sher", "single") + row("Aaya Sher", "album"))
    result = controller._select_spotify_track_once("Aaya Sher", "")
    assert result["status"] == "selected"
    assert controller.evaluate("() => window.played") == ["single"]


def test_artist_selects_correct_track(controller):
    load(
        controller,
        row("Aaya Sher", "one", "Artist One") + row("Aaya Sher", "two", "Artist Two"),
    )
    result = controller._select_spotify_track_once("Aaya Sher", "Artist Two")
    assert result["track_path"] == "/track/two"
    assert controller.evaluate("() => window.played") == ["two"]


def test_substring_or_remix_is_not_requested_song(controller):
    load(controller, row("Aaya Sher Remix", "wrong"))
    assert controller.evaluate(SELECT_TRACK, ["Aaya Sher", ""])["status"] == "not-found"
    assert controller.evaluate("() => window.played") == []


def player(title="Aaya Sher", track="right", playing=True):
    state = "Pause" if playing else "Play"
    return f'<footer><div data-testid="now-playing-widget"><a href="https://open.spotify.com/track/{track}" data-testid="context-item-info-title">{title}</a><a href="https://open.spotify.com/artist/one">Example Artist</a></div><button data-testid="control-button-playpause" aria-label="{state}" onclick="window.played.push(\'toggle\'); this.setAttribute(\'aria-label\', this.getAttribute(\'aria-label\')===\'Pause\'?\'Play\':\'Pause\');">{state}</button></footer>'


def test_player_observation_identifies_actual_song(controller):
    load(controller, row("Aaya Sher", "right") + player("Different Song", "wrong"))
    state = controller.evaluate(PLAYER_STATE)
    assert state["playing"] is True
    assert state["track_path"] == "/track/wrong"
    assert state["title"] == "Different Song"


@pytest.mark.parametrize("action,playing", [("play", True), ("pause", False)])
def test_play_pause_is_idempotent(controller, action, playing):
    load(controller, player(playing=playing))
    assert "Player state observed" in controller.spotify_control(action)
    assert controller.evaluate("() => window.played") == []


def test_missing_player_never_verifies_playback(controller):
    load(controller, row("Aaya Sher", "right"))
    state = controller.spotify_player_state()
    assert state["available"] is False and state["playing"] is False


def test_current_spotify_aside_player_has_track_identity(controller):
    load(
        controller,
        player()
        .replace("<footer>", '<aside data-testid="now-playing-bar">')
        .replace("</footer>", "</aside>")
        .replace('data-testid="now-playing-widget"', 'class="current-player-widget"'),
    )
    state = controller.spotify_player_state()
    assert state["title"] == "Aaya Sher"
    assert state["track_path"] == "/track/right"


def test_disabled_placeholder_pause_button_is_not_playback(controller):
    load(
        controller,
        '<aside data-testid="now-playing-bar"><button disabled data-testid="control-button-playpause" aria-label="Pause"></button></aside>',
    )
    state = controller.spotify_player_state()
    assert state["available"] is False and state["playing"] is False


def test_track_play_uses_a_trusted_browser_click(controller, monkeypatch):
    markup = row("Aaya Sher", "right").replace(
        "window.played.push('right')",
        "window.played.push(event.isTrusted); document.querySelector('[data-testid=control-button-playpause]').setAttribute('aria-label','Pause')",
    )
    load(controller, markup + player(playing=False))
    monkeypatch.setattr(controller._page, "goto", lambda *a, **k: None)
    assert "Playback observed" in controller.spotify_play_track("Aaya Sher")
    assert controller.evaluate("() => window.played") == [True]


def test_current_v2_player_album_link_exposes_song_identity(controller):
    markup = (
        player()
        .replace("now-playing-widget", "now-playing-widget-v2")
        .replace(
            'href="https://open.spotify.com/track/right" data-testid="context-item-info-title"',
            'href="https://open.spotify.com/album/release" data-testid="context-item-link"',
        )
    )
    load(controller, markup)
    state = controller.spotify_player_state()
    assert state["title"] == "Aaya Sher"
    assert state["artists"] == ["Example Artist"]
    assert state["track_path"] == ""  # Do not invent a URI from the selection.


@pytest.mark.parametrize("action,playing", [("play", False), ("pause", True)])
def test_controls_use_trusted_input_when_state_changes(controller, action, playing):
    load(
        controller,
        player(playing=playing).replace(
            "window.played.push('toggle')", "window.played.push(event.isTrusted)"
        ),
    )
    assert "Player state observed" in controller.spotify_control(action)
    assert controller.evaluate("() => window.played") == [True]


def test_playlist_play_uses_trusted_input(controller, monkeypatch):
    main = "<main><a href=\"https://open.spotify.com/playlist/mix\">Mix</a><button aria-label=\"Play Mix\" onclick=\"window.played.push(event.isTrusted);document.querySelector('[data-testid=control-button-playpause]').setAttribute('aria-label','Pause')\">Play</button></main>"
    markup = "<script>window.played=[];</script>" + main + player(playing=False)

    def attach_route():
        controller._page.route(
            "https://open.spotify.com/**",
            lambda route: route.fulfill(
                status=200, content_type="text/html", body=markup
            ),
        )

    controller._worker.run(attach_route)
    monkeypatch.setattr(controller, "playlist_context_matches", lambda: True)
    try:
        assert "Playback observed" in controller.spotify_play_playlist("Mix")
        assert controller.evaluate("() => window.played") == [True]
    finally:
        controller._worker.run(
            lambda: controller._page.unroute("https://open.spotify.com/**")
        )
