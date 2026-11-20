"""Spotify voice tools with a fresh, service-specific browser preference."""

import asyncio
import os
import re
from urllib.parse import urlsplit

from livekit.agents import RunContext, ToolError, function_tool
from livekit.agents.llm import ToolFlag

from host_execution import HostActionTimeout
from request_scope import ScopeViolation
from request_scope_livekit import check_current_action


class SpotifyTools:
    @staticmethod
    def _is_spotify_url(url):
        target = url.strip()
        if "://" not in target:
            target = "https://" + target
        return urlsplit(target).hostname == "open.spotify.com"

    async def _spotify_browser(self, browser=""):
        # Read canonical preferences for every action so a saved correction or
        # clearing a profile takes effect without waiting for a prompt refresh.
        if self._personal_memory is not None:
            profile = await asyncio.to_thread(self._personal_memory.get_profile)
            preferences = profile.get("preferences") or ""
        else:
            preferences = "\n".join(
                await asyncio.to_thread(self._dual_memory.list_entries, "user")
            )
        candidates = []
        excluded = set()
        exclusive = set()
        for sentence in re.split(r"[\n.;]|\band\b", preferences, flags=re.I):
            if re.search(r"\bspotify\b", sentence, re.I):
                negative = re.findall(
                    r"\b(?:don't|do not|never|avoid|not|instead of|rather than)\s+(?:use\s+)?(brave|chrome)\b",
                    sentence,
                    re.I,
                )
                excluded.update(name.lower() for name in negative)
                for before, after in re.findall(
                    r"\b(brave|chrome)\s+only\b|\bonly\s+(?:use\s+)?(brave|chrome)\b",
                    sentence,
                    re.I,
                ):
                    exclusive.add((before or after).lower())
                candidates.extend(
                    name
                    for name in re.findall(r"\b(brave|chrome)\b", sentence, re.I)
                    if name.lower() not in excluded
                )
        choices = exclusive or {
            name.lower() for name in candidates if name.lower() not in excluded
        }
        if len(choices) > 1 or choices.intersection(excluded):
            raise ToolError(
                "Saved Spotify browser preferences conflict. Ask which browser to use and update the preference."
            )
        preferred = (
            next(iter(choices), "")
            or os.environ.get("IRA_SPOTIFY_BROWSER", "").strip().lower()
        )
        selected = (
            preferred
            or browser.strip().lower()
            or getattr(self._browser, "_browser", "chrome")
        )
        if selected not in {"brave", "chrome"}:
            raise ToolError("Spotify browser must be Brave or Chrome.")
        if selected in excluded:
            raise ToolError(
                f"The Spotify preference excludes {selected.title()}. Ask which browser to use."
            )
        if preferred and browser and browser.strip().lower() != preferred:
            raise ToolError(
                f"The saved Spotify preference requires {preferred.title()}. Use that browser or update the preference on the user's request."
            )
        return selected

    async def _spotify_action(self, context, operation, browser=""):
        selected = await self._spotify_browser(browser)
        reporter = self._reporter.for_tool(context)
        reporter.emit("RUNNING", "Spotify")

        def execute():
            # BrowserController.open_spotify enforces this binary, and never
            # falls back to a system browser. Reuse an existing Spotify tab.
            current_url = getattr(self._browser, "current_url", "") or ""
            check_current_action()
            if (
                getattr(self._browser, "_browser", None) != selected
                or urlsplit(current_url).hostname != "open.spotify.com"
            ):
                self._browser.open_spotify(selected)
            check_current_action()
            return operation()

        try:
            result = await self._run_host_action(execute)
        except (ScopeViolation, HostActionTimeout):
            reporter.emit("ERROR", "Spotify execution stopped")
            raise
        except Exception as exc:
            reporter.emit("ERROR", "Spotify")
            raise ToolError(
                f"Spotify failed in {selected.title()}: {exc}. Inspect the player; do not report playback as successful."
            ) from exc
        reporter.emit("DONE", "Spotify result observed")
        return result

    @function_tool(flags=ToolFlag.CANCELLABLE, on_duplicate="reject")
    async def open_spotify(self, context: RunContext, browser: str = "") -> str:
        """Open Spotify in the saved preferred browser (Brave/Chrome). This only opens the player; it does not play a song. Preserve login in IRA's persistent browser profile."""
        selected = await self._spotify_browser(browser)
        try:
            return await self._run_host_action(self._browser.open_spotify, selected)
        except (ScopeViolation, HostActionTimeout):
            raise
        except Exception as exc:
            raise ToolError(
                f"Could not open Spotify in {selected.title()}: {exc}"
            ) from exc

    @function_tool(flags=ToolFlag.CANCELLABLE, on_duplicate="reject")
    async def spotify_play_track(
        self, context: RunContext, name: str, artist: str = "", browser: str = ""
    ) -> str:
        """Play the requested song on Spotify using the saved browser preference. Matches song/artist and verifies now-playing identity and active playback. Ambiguous results require artist clarification; login/unverified playback is a failure, never success."""
        return await self._spotify_action(
            context, lambda: self._browser.spotify_play_track(name, artist), browser
        )

    @function_tool(flags=ToolFlag.CANCELLABLE, on_duplicate="reject")
    async def spotify_play_playlist(
        self, context: RunContext, name: str, browser: str = ""
    ) -> str:
        """Play a named Spotify playlist in the preferred browser, requiring observed playback and matching playlist context."""
        return await self._spotify_action(
            context, lambda: self._browser.spotify_play_playlist(name), browser
        )

    @function_tool(flags=ToolFlag.CANCELLABLE, on_duplicate="reject")
    async def spotify_control(
        self, context: RunContext, action: str, seconds: int = 10, browser: str = ""
    ) -> str:
        """Control Spotify: play/pause verifies the resulting player state; rewind/forward shifts seconds. Uses the saved preferred browser."""
        return await self._spotify_action(
            context, lambda: self._browser.spotify_control(action, seconds), browser
        )
