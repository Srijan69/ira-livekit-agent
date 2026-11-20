"""Spotify selection and verified playback through native Playwright input."""

import time
import unicodedata
from urllib.parse import quote, urlsplit

from browser_runtime import ACTION_TIMEOUT, browser_call
from spotify_dom import PLAYER_STATE, SELECT_TRACK


def _norm(value):
    return " ".join(unicodedata.normalize("NFKC", value or "").split()).casefold()


class SpotifyBrowser:
    def _pause_browser_poll(self, milliseconds=200):
        self._check_action()
        self._require_page().wait_for_timeout(milliseconds)
        self._check_action()

    @browser_call
    def spotify_player_state(self):
        result = self._require_page().evaluate(PLAYER_STATE)
        return result if isinstance(result, dict) else {}

    @browser_call
    def playback_active(self):
        state = self.spotify_player_state()
        return state.get("available") is True and state.get("playing") is True

    @browser_call
    def playlist_context_matches(self):
        return (
            self._require_page().evaluate("""() => {
            const path = location.pathname;
            if (!path.startsWith('/playlist/')) return false;
            const player = document.querySelector('[data-testid="now-playing-bar"], footer');
            return !!player && [...player.querySelectorAll('a[href*="/playlist/"]')].some(a =>
                new URL(a.href,location.href).pathname === path);
        }""")
            is True
        )

    @browser_call
    def spotify_control(self, action, seconds=10):
        action = (action or "").strip().lower()
        if action not in {"play", "pause", "rewind", "forward"}:
            raise ValueError("Spotify action must be play, pause, rewind, or forward")
        seconds = max(1, min(int(seconds), 600))
        page = self._require_page()
        self._check_action()
        if action in {"play", "pause"}:
            state = self.spotify_player_state()
            if not state.get("available"):
                raise RuntimeError("Spotify has no audio player on this page")
            desired = action == "play"
            if state.get("playing") is not desired:
                controls = page.locator(
                    '[data-testid="control-button-playpause"]'
                ).element_handles()
                buttons = [
                    button
                    for button in controls
                    if button.is_visible() and button.is_enabled()
                ]
                if len(buttons) != 1:
                    raise RuntimeError("Spotify has no unique available player control")
                self._native_click(buttons[0])
            for _ in range(20):
                self._check_action()
                state = self.spotify_player_state()
                if state.get("available") and state.get("playing") is desired:
                    return f"Spotify {'playing' if desired else 'paused'}. Player state observed."
                self._pause_browser_poll()
            raise RuntimeError(f"Spotify {action} could not be verified")
        delta = -seconds if action == "rewind" else seconds
        result = page.evaluate(
            """delta => {
            const audio = document.querySelector('audio');
            if (!audio) return null;
            const target = Math.max(0, Math.min(Number.isFinite(audio.duration) ? audio.duration : Infinity, audio.currentTime + delta));
            audio.currentTime = target;
            return {position:audio.currentTime, target};
        }""",
            delta,
        )
        if not isinstance(result, dict):
            raise RuntimeError("Spotify has no audio player on this page")
        if abs(result["position"] - result["target"]) > 0.5:
            raise RuntimeError("Spotify seek could not be verified")
        return f"Spotify {'rewound' if delta < 0 else 'advanced'} {seconds} seconds."

    @browser_call
    def spotify_play_playlist(self, name):
        target = " ".join((name or "").split())
        if not target:
            raise ValueError("Spotify playlist name cannot be empty")
        page = self._ensure_page()
        self._check_action()
        page.goto(
            f"https://open.spotify.com/search/{quote(target, safe='')}/playlists",
            wait_until="domcontentloaded",
        )
        self._clear_elements()
        selected = None
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            self._check_action()
            matches = {}
            for link in page.locator('a[href*="/playlist/"]').element_handles():
                if not link.is_visible():
                    continue
                metadata = link.evaluate(
                    "node=>({name:(node.innerText||node.getAttribute('aria-label')||'').trim(),href:node.href})"
                )
                if _norm(metadata["name"]) == _norm(target):
                    matches[urlsplit(metadata["href"]).path] = link
            if len(matches) > 1:
                raise LookupError(
                    "Several playlists share that name. Ask which playlist to use; no playlist was played."
                )
            if matches:
                path, selected = next(iter(matches.items()))
                break
            self._pause_browser_poll()
        if selected is None:
            raise LookupError(f"Spotify playlist '{target}' was not found")
        search_url = page.url
        selected_href = selected.get_attribute("href")

        def link_preflight():
            metadata = selected.evaluate(
                "node=>({name:(node.innerText||node.getAttribute('aria-label')||'').trim(),href:node.href})"
            )
            if (
                page.url != search_url
                or _norm(metadata["name"]) != _norm(target)
                or urlsplit(metadata["href"]).path != path
                or selected.get_attribute("href") != selected_href
            ):
                raise RuntimeError("Spotify playlist target changed before input.")

        self._native_click(selected, preflight=link_preflight)
        deadline = time.monotonic() + 6
        button = None
        while time.monotonic() < deadline:
            self._check_action()
            if urlsplit(page.url).path == path:
                buttons = page.locator("main button").element_handles()
                candidates = [
                    b
                    for b in buttons
                    if b.is_visible()
                    and b.is_enabled()
                    and _norm(
                        b.get_attribute("aria-label") or b.inner_text()
                    ).startswith("play")
                ]
                if len(candidates) == 1:
                    button = candidates[0]
                    break
            self._pause_browser_poll()
        if button is None:
            raise RuntimeError(
                f"Spotify found '{target}' but playback was not available"
            )

        def play_preflight():
            if urlsplit(page.url).path != path:
                raise RuntimeError(
                    "Spotify playlist page changed before playback input."
                )

        self._native_click(button, preflight=play_preflight)
        for _ in range(30):
            self._check_action()
            if self.playback_active() and self.playlist_context_matches():
                return f"Playing Spotify playlist {target}. Playback observed."
            self._pause_browser_poll()
        raise RuntimeError(
            f"Spotify selected '{target}', but playback could not be verified"
        )

    @browser_call
    def _select_spotify_track_once(self, name, artist=""):
        page = self._require_page()
        self._check_action()
        result = page.evaluate(SELECT_TRACK, [name, artist])
        if not isinstance(result, dict):
            return {}
        if result.get("status") != "ready":
            return result
        row = (
            page.locator('[data-testid="tracklist-row"]')
            .nth(result["row_index"])
            .element_handle()
        )
        if row is None:
            return {"status": "not-found"}
        row.scroll_into_view_if_needed(timeout=ACTION_TIMEOUT)
        self._check_action()
        row.hover(timeout=ACTION_TIMEOUT)
        self._check_action()
        search_url = page.url

        def row_preflight():
            metadata = row.evaluate(
                """node=>({connected:node.isConnected,title:(node.querySelector('a[href*="/track/"]')?.innerText||'').trim(),artists:[...node.querySelectorAll('a[href*="/artist/"]')].map(a=>a.innerText.trim()),track_path:((link)=>link?new URL(link.href,location.href).pathname:'')(node.querySelector('a[href*="/track/"]'))})"""
            )
            current = (
                page.locator('[data-testid="tracklist-row"]')
                .nth(result["row_index"])
                .element_handle()
            )
            if (
                page.url != search_url
                or current is None
                or not row.evaluate("(node,other)=>node===other", current)
                or not metadata["connected"]
                or _norm(metadata["title"]) != _norm(result["title"])
                or {_norm(a) for a in metadata["artists"]}
                != {_norm(a) for a in result["artists"]}
                or metadata["track_path"] != result["track_path"]
            ):
                raise RuntimeError(
                    "Spotify search target changed before input. Inspect the current results."
                )

        row_preflight()
        buttons = row.query_selector_all("button")
        playable = [
            button
            for button in buttons
            if _norm(button.get_attribute("aria-label")).startswith("play")
            and button.is_enabled()
        ]
        if len(playable) != 1:
            return {"status": "control-unavailable"}
        self._native_click(playable[0], preflight=row_preflight)
        result["status"] = "selected"
        return result

    @staticmethod
    def _spotify_identity_matches(selected, state):
        if not _norm(selected.get("title")) or _norm(state.get("title")) != _norm(
            selected.get("title")
        ):
            return False
        if state.get("track_path"):
            return state["track_path"] == selected.get("track_path")
        artists = {_norm(a) for a in state.get("artists", []) if a}
        return bool(artists) and artists == {
            _norm(a) for a in selected.get("artists", []) if a
        }

    @browser_call
    def spotify_play_track(self, name, artist=""):
        track_name = " ".join((name or "").split())
        artist_name = " ".join((artist or "").split())
        if not track_name:
            raise ValueError("Spotify track name cannot be empty")
        page = self._ensure_page()
        query = f"{track_name} {artist_name}".strip()
        self._check_action()
        page.goto(
            f"https://open.spotify.com/search/{quote(query, safe='')}/tracks",
            wait_until="domcontentloaded",
        )
        self._clear_elements()
        selected = None
        for _ in range(24):
            self._check_action()
            result = self._select_spotify_track_once(track_name, artist_name)
            if result.get("status") == "ambiguous":
                raise LookupError(
                    f"Several Spotify tracks match '{query}'. Ask for the artist; no track was played."
                )
            if result.get("status") == "control-unavailable":
                raise RuntimeError(
                    "Matching Spotify track has no observed play control. Inspect the page or sign in."
                )
            if result.get("status") == "selected":
                selected = result
                break
            self._pause_browser_poll()
        if selected is None:
            raise LookupError(
                f"Spotify track '{query}' was not found as an exact match. Inspect search results or login; no track was played."
            )
        initial_position = None
        for _ in range(30):
            self._check_action()
            state = self.spotify_player_state()
            if state.get("playing") is True and self._spotify_identity_matches(
                selected, state
            ):
                position = state.get("position_seconds")
                if isinstance(position, (int, float)) and not isinstance(
                    position, bool
                ):
                    if initial_position is None:
                        initial_position = position
                    if position <= initial_position:
                        self._pause_browser_poll()
                        continue
                title = f"'{state['title']}'"
                if state.get("artists"):
                    title += " by " + ", ".join(state["artists"])
                return f"Playing {title} on Spotify in {self._browser.title()}. Playback observed."
            self._pause_browser_poll()
        raise RuntimeError(
            f"Spotify selected '{query}', but the requested track's playback could not be verified. Inspect the player or login; do not repeat the play click blindly."
        )
