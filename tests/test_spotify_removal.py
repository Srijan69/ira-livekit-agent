"""Dedicated playback retirement preserves explicitly requested generic actions."""

from concurrent.futures import CancelledError
from pathlib import Path
from shutil import copytree, ignore_patterns
from types import SimpleNamespace

import pytest
from livekit.agents.llm import ToolContext
from livekit.agents.voice.speech_handle import SpeechHandle

from agent import _INSTRUCTIONS, Assistant
from browser import BrowserUnavailableError
from context_instructions import context_instructions
from dual_memory import DualMemoryStore
from request_scope import ScopeViolation
from skill_adapters import SkillToolAdapters
from skill_execution import SkillExecutionService
from skill_format import Action, Check, Skill, SkillInput, Step
from skill_runs import SkillRunStore
from skill_store import SkillStore
from skills import SkillCatalog
from tests.fixtures.playwright_fakes import FakePage, controller_with

RETIRED_TOOLS = {
    "open_spotify",
    "spotify_play_track",
    "spotify_play_playlist",
    "spotify_control",
}


def test_registered_provider_tools_and_both_prompt_paths_retire_playback(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GOOGLE_API_KEY", "offline-test-key")
    copytree(
        Path(__file__).resolve().parents[1] / "skills",
        tmp_path / "skills",
        ignore=ignore_patterns("*.sqlite3", "*.sqlite3-*"),
    )
    catalog = SkillCatalog(tmp_path / "skills")
    assistant = Assistant(
        skills_catalog=catalog,
        skills=SkillStore(tmp_path / "workflows"),
        dual_memory=DualMemoryStore(tmp_path / "memory"),
    )
    schema = ToolContext(assistant.tools).parse_function_tools(
        "google", use_parameters_json_schema=True
    )
    names = {tool.info.name for tool in assistant.tools}
    assert RETIRED_TOOLS.isdisjoint(names)
    assert {"browse_to", "new_tab", "read_page", "open_app"} <= names
    for rendered in (
        str(schema),
        _INSTRUCTIONS,
        context_instructions(),
        catalog.render_prompt_catalog(),
    ):
        assert not any(name in rendered for name in RETIRED_TOOLS)
        assert "play-spotify" not in rendered
    assert catalog.get_skill("media/play-spotify") is None


@pytest.mark.parametrize("name", sorted(RETIRED_TOOLS))
async def test_old_compiled_playback_names_are_unavailable_before_effect(name):
    effects = []
    browser = SimpleNamespace(
        open_spotify=lambda browser: effects.append(("open", browser)),
        spotify_play_playlist=lambda name: effects.append(("playlist", name)),
        spotify_control=lambda action, seconds: effects.append(("control", action)),
    )
    adapters = SkillToolAdapters(browser, SimpleNamespace(), effects.append)
    arguments = {
        "open_spotify": {"browser": "brave"},
        "spotify_play_playlist": {"name": "Old playlist"},
        "spotify_control": {"action": "play"},
        "spotify_play_track": {"name": "Old song"},
    }
    with pytest.raises(ValueError, match="not available"):
        await adapters.registry().call(name, arguments[name])
    assert effects == []


@pytest.mark.parametrize(
    "error",
    [
        ScopeViolation("off goal"),
        CancelledError("stopped"),
        BrowserUnavailableError("missing Brave"),
    ],
)
def test_browser_fallback_cannot_swallow_scope_cancellation_or_unavailability(
    error, monkeypatch
):
    effects = []

    def navigate(url):
        raise error

    monkeypatch.setattr("webbrowser.open", effects.append)
    adapters = SkillToolAdapters(
        SimpleNamespace(navigate=navigate), SimpleNamespace(), effects.append
    )
    with pytest.raises(type(error)):
        adapters.browse_to("https://example.test")
    assert effects == []


def test_compiled_navigation_timeout_after_commit_does_not_repeat_in_system_browser(
    monkeypatch,
):
    page = FakePage(url="https://example.test/old")
    browser = controller_with(page)
    fallbacks = []
    monkeypatch.setattr("webbrowser.open", fallbacks.append)

    def timeout_after_commit(url):
        raise TimeoutError("page committed but DOM content loading timed out")

    page.on_goto = timeout_after_commit
    adapters = SkillToolAdapters(browser, SimpleNamespace(), lambda name: None)
    try:
        with pytest.raises(RuntimeError, match="Inspect the current page"):
            adapters.browse_to("https://example.test/requested")
        assert page.url == "https://example.test/requested"
        assert page.visited == ["https://example.test/requested"]
        assert fallbacks == []
    finally:
        browser.close()


def test_generic_navigation_to_explicit_service_url_keeps_selected_browser():
    page = FakePage(url="https://example.test")
    browser = controller_with(page)
    browser._browser = "brave"
    try:
        browser.navigate("https://open.spotify.com")
        assert page.visited == ["https://open.spotify.com"]
        assert browser._browser == "brave"
    finally:
        browser.close()


def test_generic_app_adapter_accepts_explicit_application_name():
    effects = []
    adapters = SkillToolAdapters(SimpleNamespace(), SimpleNamespace(), effects.append)
    result = adapters.open_app("Spotify")
    assert result.ok
    assert effects == ["Spotify"]


@pytest.mark.parametrize("tool_name", ["browse_to", "new_tab"])
async def test_exported_generic_navigation_uses_current_target_without_old_service_recipe(
    tool_name, tmp_path, monkeypatch
):
    monkeypatch.setenv("GOOGLE_API_KEY", "offline-test-key")
    effects = []

    class Browser:
        _browser = "chrome"

        def navigate(self, url):
            effects.append((self._browser, url))
            return "Requested page observed"

        def new_tab(self, url):
            return self.navigate(url)

    memory = DualMemoryStore(tmp_path / "memory")
    memory.add("user", "Use Brave only for Spotify and play the old playlist.")
    assistant = Assistant(
        browser=Browser(),
        dual_memory=memory,
        skills=SkillStore(tmp_path / "workflows"),
        skills_catalog=SkillCatalog(tmp_path / "skills"),
    )
    request = assistant._request_scope.begin_request(
        "Open https://open.spotify.com", source_id="human:explicit-navigation"
    )
    speech = SpeechHandle.create()
    assistant._scoped_tools.bind_speech(speech, request)
    context = SimpleNamespace(
        speech_handle=speech,
        function_call=SimpleNamespace(call_id="explicit-navigation"),
    )
    tool = next(tool for tool in assistant.tools if tool.info.name == tool_name)
    result = await tool(context, url="https://open.spotify.com")
    assert "Requested page observed" in result
    assert effects == [("chrome", "https://open.spotify.com")]


def test_legacy_playlist_input_alias_cannot_replace_declared_slot(tmp_path):
    adapters = SkillToolAdapters(
        SimpleNamespace(), SimpleNamespace(), lambda name: None
    )
    service = SkillExecutionService(
        verifier=adapters.verify, run_store=SkillRunStore(tmp_path / "runs.db")
    )
    candidate = Skill(
        "Open requested item",
        "Open the named application",
        state="enabled",
        allowed_tools=("open_app",),
        inputs=(SkillInput("playlist", "Application name"),),
        steps=(
            Step(
                1,
                "Open app",
                action=Action(
                    "tool", tool="open_app", args=(("app_name", "{playlist}"),)
                ),
                check=Check("window_title", "{playlist}"),
            ),
        ),
    )
    with pytest.raises(ValueError, match=r"undeclared|unknown"):
        service.validate(candidate, adapters.registry(), inputs={"name": "Calculator"})
