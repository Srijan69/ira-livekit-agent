"""Gemini Live routing for the confirmed artist, with isolated browser effects."""

import json
import os

import pytest
from livekit.agents import AgentSession

from agent import Assistant
from dual_memory import DualMemoryStore
from skill_store import SkillStore
from skills import SkillCatalog

pytestmark = pytest.mark.skipif(
    os.environ.get("IRA_RUN_LIVE_EVALS") != "1" or not os.environ.get("GOOGLE_API_KEY"),
    reason="Live Gemini evaluation is opt-in",
)


@pytest.mark.parametrize(
    "request_text,full_schema",
    [
        (
            "Play Aaya Sher by Anirudh Ravichander on Spotify. Use Brave only for Spotify.",
            False,
        ),
        ("Play Aaya Sher by Anirudh Ravichander on Spotify.", False),
        (
            "Play Aaya Sher by Anirudh Ravichander on Spotify. Use Brave only for Spotify.",
            True,
        ),
        ("Play Aaya Sher by Anirudh Ravichander on Spotify.", True),
    ],
)
async def test_live_model_uses_available_spotify_tools_and_brave(
    tmp_path, request_text, full_schema, monkeypatch
):
    monkeypatch.delenv("IRA_SPOTIFY_BROWSER", raising=False)
    effects = []

    class Browser:
        _browser = "chrome"
        is_open = False
        current_url = ""

        def open_spotify(self, browser=None):
            effects.append(("open", browser))
            self._browser = browser
            return "Spotify web player opened in Brave. No song has played yet."

        def spotify_play_track(self, name, artist=""):
            # Mirror the real browser's disambiguation boundary instead of
            # pretending that an unqualified title has already matched.
            if not artist:
                raise RuntimeError(
                    "Multiple songs match Aaya Sher: Anirudh Ravichander and Anshu Gupta. Specify the artist."
                )
            if (
                name.casefold() != "aaya sher"
                or artist.casefold() != "anirudh ravichander"
            ):
                raise RuntimeError("Requested track/artist was not found.")
            effects.append(("track", name, artist))
            return f"Playing '{name}' by {artist} on Spotify in Brave. Playback observed; now-playing title and artist match."

    memory = DualMemoryStore(tmp_path / "memory")
    memory.add("user", "Use Brave only for Spotify.")
    from personal_memory import PersonalMemoryStore

    personal = PersonalMemoryStore(tmp_path / "personal.db") if full_schema else None
    if personal:
        personal.save_profile(
            {"preferences": "Use Brave only for Spotify."}, source="explicit"
        )
    agent = Assistant(
        browser=Browser(),
        dual_memory=memory,
        personal_memory=personal,
        skills=SkillStore(tmp_path / "workflows"),
        skills_catalog=SkillCatalog(),
    )
    # These are the real scoped/repaired callable wrappers. Limit this eval's
    # schemas to relevant tools so unexpected model calls cannot touch the host.
    allowed = {
        "skills_list",
        "skill_view",
        "list_skills",
        "view_skill",
        "run_skill",
        "spotify_play_track",
        "open_spotify",
        "list_preferences",
        "finish_request",
        "clarify_request",
    }
    if not full_schema:
        agent._tools = [t for t in agent.tools if t.info.name in allowed]
    else:
        # Full production schemas with host execution intercepted: model calls
        # are still admitted/dispatched, but only the fake Spotify host can run.
        run_host = agent._run_host_action

        async def isolated_host(fn, *args, **kwargs):
            if (
                getattr(fn, "__self__", None) is agent._browser
                or getattr(fn, "__name__", None) == "execute"
            ):
                return await run_host(fn, *args, **kwargs)
            raise RuntimeError("Host action outside this evaluation's Spotify fixture")

        agent._run_host_action = isolated_host
    request = agent._request_scope.begin_request(
        request_text, source_id="evaluation:human:1"
    )
    async with AgentSession() as session:
        await session.start(agent)

        # session.run is the SDK's trusted test-input boundary; production uses
        # text_input_cb. Bind only the corresponding test-created speech.
        def bind(event):
            if event.user_initiated:
                agent._scoped_tools.bind_speech(event.speech_handle, request)

        session.on("speech_created", bind)
        result = await session.run(user_input=request_text)
        calls = [
            (e.item.name, json.loads(e.item.arguments))
            for e in result.events
            if e.type == "function_call"
        ]
    assert any(name == "spotify_play_track" for name, _ in calls), calls
    assert ("open", "brave") in effects, effects
    assert any(
        e[0] == "track"
        and e[1].lower() == "aaya sher"
        and e[2].lower() == "anirudh ravichander"
        for e in effects
    ), effects
    assert all(e[1] == "brave" for e in effects if e[0] == "open"), effects
