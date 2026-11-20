"""Behavioral regressions for the advertised procedural route and Spotify case."""

import json
from types import SimpleNamespace

import pytest
from livekit.agents import ToolError
from livekit.agents.voice.speech_handle import SpeechHandle

from agent import Assistant
from browser import BrowserController, _launch_options
from dual_memory import DualMemoryStore
from skill_store import SkillStore
from skills import SkillCatalog
from tests.fixtures.playwright_fakes import FakeContext, FakePage, controller_with
from tool_dispatch import repair_json_string


def assistant(tmp_path, **kwargs):
    catalog = SkillCatalog(tmp_path / "skills")
    catalog.create_skill(
        "play-spotify", "Use spotify_play_track and verify playback.", category="media"
    )
    return Assistant(
        skills_catalog=catalog,
        skills=SkillStore(tmp_path / "workflows"),
        dual_memory=DualMemoryStore(tmp_path / "memory"),
        **kwargs,
    )


async def test_advertised_procedure_is_loadable_through_public_list_view_run(tmp_path):
    agent = assistant(tmp_path)
    assert "play-spotify" in await agent.list_skills(None)
    assert "spotify_play_track" in await agent.view_skill(None, "play-spotify")
    output = await agent.run_skill(None, "play-spotify", '{"name":"Aaya Sher"}')
    assert "spotify_play_track" in output
    assert "procedure_ready" in output
    assert "completed" not in output.lower()
    assert agent._skill_service is None  # A procedure is followed by the model.


def test_default_compiled_store_is_distinct_from_procedure_catalog(tmp_path):
    catalog = SkillCatalog(tmp_path / "skills")
    agent = Assistant(
        skills_catalog=catalog, dual_memory=DualMemoryStore(tmp_path / "memory")
    )
    assert isinstance(agent._skills, SkillStore)
    assert agent._skills is not catalog


def test_valid_json_and_nested_payload_are_preserved_byte_for_byte():
    raw = '{"message":"Keep comma, ] and comma, } and escaped \\"quote\\"", "inputs_json":"{\\"text\\":\\"x, ]\\"}"}'
    assert json.loads(repair_json_string(raw)) == json.loads(raw)


def test_trailing_comma_repair_preserves_strings():
    assert json.loads(repair_json_string('{"message":"x, ]", "items":[1,2,],}')) == {
        "message": "x, ]",
        "items": [1, 2],
    }


def test_spotify_tools_are_present_in_actual_sdk_schema(tmp_path):
    from livekit.agents.llm import ToolContext

    agent = assistant(tmp_path)
    schema = ToolContext(agent.tools).parse_function_tools(
        "google", use_parameters_json_schema=True
    )
    names = {tool.info.name for tool in agent.tools}
    assert {
        "open_spotify",
        "spotify_play_track",
        "spotify_play_playlist",
        "spotify_control",
    } <= names
    assert "spotify_play_track" in str(schema)


async def test_exported_spotify_call_honors_saved_brave_preference(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("IRA_SPOTIFY_BROWSER", raising=False)
    effects = []

    class Browser:
        def open_spotify(self, browser=None):
            effects.append(("open", browser))
            return "Spotify opened."

        def spotify_play_track(self, name, artist=""):
            effects.append(("track", name, artist))
            return "Playing Aaya Sher on Spotify. Playback observed."

    agent = assistant(tmp_path, browser=Browser())
    agent._dual_memory.add("user", "Use Brave only for Spotify.")
    request = agent._request_scope.begin_request(
        "Play Aaya Sher on Spotify", source_id="human:spotify"
    )
    speech = SpeechHandle.create()
    agent._scoped_tools.bind_speech(speech, request)
    ctx = SimpleNamespace(
        speech_handle=speech, function_call=SimpleNamespace(call_id="spotify-1")
    )
    tool = next(t for t in agent.tools if t.info.name == "spotify_play_track")
    result = await tool(ctx, name="Aaya Sher")
    assert effects == [("open", "brave"), ("track", "Aaya Sher", "")]
    assert "Playback observed" in result


def test_missing_brave_never_uses_chrome(monkeypatch, tmp_path):
    monkeypatch.setattr("browser.os.path.isfile", lambda path: False)
    monkeypatch.setattr("browser.shutil.which", lambda name: None)
    monkeypatch.delenv("IRA_BRAVE_EXECUTABLE", raising=False)
    with pytest.raises(RuntimeError, match="Brave"):
        _launch_options(
            {
                "browser": "brave",
                "user_data_dir": str(tmp_path),
                "profile_directory": "Default",
            },
            True,
        )


def test_playing_wrong_song_is_failure(monkeypatch):
    controller = controller_with()
    monkeypatch.setattr(
        controller,
        "_select_spotify_track_once",
        lambda *a: {
            "status": "selected",
            "title": "Aaya Sher",
            "artists": ["Example Artist"],
            "track_path": "/track/right",
        },
    )
    monkeypatch.setattr(
        controller,
        "spotify_player_state",
        lambda: {
            "available": True,
            "playing": True,
            "title": "Different Song",
            "artists": ["Example Artist"],
            "track_path": "/track/wrong",
        },
    )
    with pytest.raises(RuntimeError, match="verified"):
        controller.spotify_play_track("Aaya Sher")
    controller.close()


def test_procedure_lookup_never_escapes_root_or_bypasses_platform(tmp_path):
    root = tmp_path / "skills"
    catalog = SkillCatalog(root, platform_override="windows")
    catalog.create_skill(
        "linux-only",
        "---\nname: linux-only\nplatforms: [linux]\n---\nOnly Linux",
        category="system",
    )
    outside = tmp_path / "secret"
    outside.mkdir()
    (outside / "SKILL.md").write_text("Secret procedure", encoding="utf-8")
    assert catalog.get_skill("system/linux-only") is None
    assert catalog.get_skill("../secret") is None


@pytest.mark.parametrize("tool_name", ["browse_to", "new_tab"])
async def test_general_browser_route_to_spotify_also_enforces_brave(
    tmp_path, tool_name, monkeypatch
):
    monkeypatch.delenv("IRA_SPOTIFY_BROWSER", raising=False)
    effects = []

    class Browser:
        _browser = "chrome"

        def open_spotify(self, browser):
            effects.append(("open", browser))
            self._browser = browser

        def navigate(self, url):
            effects.append(("navigate", self._browser, url))
            return "Spotify"

        def new_tab(self, url):
            return self.navigate(url)

    agent = assistant(tmp_path, browser=Browser())
    agent._dual_memory.add("user", "Use Brave only for Spotify.")
    await getattr(agent, tool_name)(
        None, url="https://open.spotify.com/search/Aaya%20Sher"
    )
    assert effects[0] == ("open", "brave")
    assert effects[-1][1] == "brave"


async def test_compound_browser_preference_does_not_assign_chrome_to_spotify(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("IRA_SPOTIFY_BROWSER", raising=False)
    agent = assistant(tmp_path)
    agent._dual_memory.add(
        "user", "Use Brave only for Spotify and Chrome for other browsing."
    )
    assert await agent._spotify_browser() == "brave"


async def test_negated_browser_does_not_override_brave_only_preference(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("IRA_SPOTIFY_BROWSER", raising=False)
    agent = assistant(tmp_path)
    agent._dual_memory.add("user", "For Spotify use Brave only, not Chrome.")
    assert await agent._spotify_browser() == "brave"


async def test_conflicting_exclusive_spotify_preferences_require_clarification(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("IRA_SPOTIFY_BROWSER", raising=False)
    agent = assistant(tmp_path)
    agent._dual_memory.add(
        "user", "Use Brave only for Spotify. Use Chrome only for Spotify."
    )
    with pytest.raises(ToolError, match="conflict"):
        await agent._spotify_browser()


def test_frozen_playback_position_never_reports_success(monkeypatch):
    browser = controller_with()
    monkeypatch.setattr(
        browser,
        "_select_spotify_track_once",
        lambda *a: {
            "status": "selected",
            "title": "Aaya Sher",
            "track_path": "/track/right",
        },
    )
    monkeypatch.setattr(
        browser,
        "spotify_player_state",
        lambda: {
            "available": True,
            "playing": True,
            "title": "Aaya Sher",
            "track_path": "/track/right",
            "position_seconds": 0,
        },
    )
    with pytest.raises(RuntimeError, match="verified"):
        browser.spotify_play_track("Aaya Sher")
    browser.close()


@pytest.mark.parametrize(
    "method,arg", [("open_spotify", "brave"), ("navigate", "https://open.spotify.com")]
)
def test_revoked_action_does_not_close_or_launch_browsers(method, arg):
    page = FakePage()
    context = FakeContext([page])
    browser = controller_with(context=context)

    def guard():
        raise RuntimeError("stopped")

    browser.input_guard = guard
    with pytest.raises(RuntimeError, match="stopped"):
        getattr(browser, method)(arg)
    assert not context.closed and not page.visited
    browser.close()


def test_v2_metadata_requires_matching_artist_identity():
    assert not BrowserController._spotify_identity_matches(
        {"title": "Aaya Sher", "artists": ["Anirudh Ravichander"]},
        {"title": "Aaya Sher", "artists": ["Anshu Gupta"], "track_path": ""},
    )


async def test_stop_after_opening_spotify_prevents_later_play(tmp_path, monkeypatch):
    monkeypatch.setenv("IRA_SPOTIFY_BROWSER", "brave")
    effects = []

    class Browser:
        def open_spotify(self, browser):
            effects.append("open")
            agent._request_scope.stop()

        def spotify_play_track(self, name, artist=""):
            effects.append("play")
            return "Playing Aaya Sher. Playback observed."

    agent = assistant(tmp_path, browser=Browser())
    request = agent._request_scope.begin_request("Play Aaya Sher", source_id="human:1")
    speech = SpeechHandle.create()
    agent._scoped_tools.bind_speech(speech, request)
    ctx = SimpleNamespace(
        speech_handle=speech, function_call=SimpleNamespace(call_id="play-stop")
    )
    tool = next(t for t in agent.tools if t.info.name == "spotify_play_track")
    with pytest.raises(ToolError):
        await tool(ctx, name="Aaya Sher")
    assert effects == ["open"]


async def test_stop_during_search_prevents_later_track_click(tmp_path, monkeypatch):
    monkeypatch.setenv("IRA_SPOTIFY_BROWSER", "brave")
    from spotify_dom import SELECT_TRACK

    effects = []

    page = FakePage(url="https://open.spotify.com")
    page.on_goto = lambda url: agent._request_scope.stop()

    def inspect(script, arg):
        if script == SELECT_TRACK:
            effects.append("play-click")
        return {}

    page.evaluator = inspect
    browser = controller_with(page)
    browser._browser = "brave"
    agent = assistant(tmp_path, browser=browser)
    request = agent._request_scope.begin_request("Play Aaya Sher", source_id="human:2")
    speech = SpeechHandle.create()
    agent._scoped_tools.bind_speech(speech, request)
    ctx = SimpleNamespace(
        speech_handle=speech, function_call=SimpleNamespace(call_id="search-stop")
    )
    tool = next(t for t in agent.tools if t.info.name == "spotify_play_track")
    with pytest.raises(ToolError):
        await tool(ctx, name="Aaya Sher")
    assert effects == []

    browser.close()
