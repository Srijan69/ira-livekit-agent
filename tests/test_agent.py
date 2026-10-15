"""Tests that this stays a realtime-only, speech-to-speech IRA agent.

The defining constraint of this project is that one Gemini Live model handles
audio in and audio out, with no text bottleneck in between. Two changes would
silently break that while still looking like working code: adding a separate
STT or TTS stage, or pointing GEMINI_LIVE_MODEL at a model that cannot do
`bidiGenerateContent`. Both are pinned down below.

The rest of the file covers the IRA persona, the pinned voice, and the
`open_app` desktop tool. The PyAutoGUI key sequence is checked against a fake
module rather than a real desktop, so these tests never move the mouse or type
into whatever window happens to be focused.

Full multi-turn conversations are covered separately by the simulations in
scenarios.yaml (see README.md).
"""

import asyncio
import json
import os
import sys
from unittest.mock import MagicMock, call, patch

import pytest
from google.genai import types as genai_types
from livekit.agents import NOT_GIVEN, AgentSession, APIConnectionError
from livekit.agents.voice.run_result import mock_tools
from livekit.plugins import google

from agent import (
    GEMINI_LIVE_MODEL,
    GEMINI_VOICE,
    PREWARM_IMPORTS,
    STATUS_BACKLOG,
    STATUS_TOPIC,
    Assistant,
    _format_seed_facts,
    _is_console_key_listener_interrupt,
    _launch_via_start_menu,
    _prewarm,
    _room_options,
    _room_status_publisher,
    _sanitize_app_name,
    server,
)
from browser import BrowserUnavailableError, PageElement
from memory import MemoryStore
from telemetry import SessionStatusReporter
from workflows import WorkflowStore

# Model ids confirmed to advertise `bidiGenerateContent` on this project's
# GOOGLE_API_KEY. Anything outside this set cannot serve a realtime session --
# notably the plugin's own default, `gemini-2.5-flash`, which cannot.
LIVE_CAPABLE_MODELS = {
    "gemini-3.1-flash-live-preview",
    "gemini-2.5-flash-native-audio-latest",
    "gemini-2.5-flash-native-audio-preview-12-2025",
}

# Models that accept generate_reply(), and so can both open the conversation
# with a greeting and be driven by the in-process eval framework (which routes
# every turn through generate_reply under the hood).
GREETING_CAPABLE_MODELS = {
    "gemini-2.5-flash-native-audio-latest",
    "gemini-2.5-flash-native-audio-preview-12-2025",
}

# Needs the live Gemini Live API, so it is skipped when no key is configured.
# agent.py calls load_dotenv('.env.local') at import, so running
# `uv run pytest` from the project root is enough to pick the key up.
requires_live_api = pytest.mark.skipif(
    not os.environ.get("GOOGLE_API_KEY"),
    reason="opens a live Gemini Live session; needs GOOGLE_API_KEY",
)

# Model that grades the judged evals. Overridable because the free tier meters
# per model per day (quota id `GenerateRequestsPerDayPerProjectPerModel`): when
# this one is spent, pointing the judge at another flash model buys a fresh
# allowance without touching the agent or waiting for a reset. It only has to
# read a short reply and answer a yes/no question, so any of them will do.
#
# Pinned to a concrete model rather than `gemini-flash-latest` on purpose: an
# alias that drifts to a new model silently changes the grading standard, and
# an eval whose bar moves on its own is not a test. Note that the listing API
# still advertises retired models -- `gemini-2.5-flash-lite` is listed and
# 404s on use -- so verify a replacement actually answers before pinning it.
JUDGE_MODEL = os.environ.get("IRA_JUDGE_MODEL", "gemini-3.5-flash")


def _judge_llm() -> google.LLM:
    """The model that grades the evals below.

    Deliberately a plain text LLM rather than the session's own: `session.llm`
    is the realtime model, and `judge()` reads `llm.model`, which a
    RealtimeModel leaves as None -- passing it crashes with an AttributeError
    that looks nothing like the real problem.
    """
    return google.LLM(model=JUDGE_MODEL)


async def _judged(assertion) -> None:
    """Await a judge assertion, skipping rather than failing when out of quota.

    The judge runs on the free tier, which meters per model per day. Each
    judged eval spends one request, so a few full-suite runs exhaust it -- and
    the 429 then surfaces as a failed assertion indistinguishable from a
    persona regression.

    Skip instead. A billing ceiling is not evidence about whether IRA is in
    character, and a suite that goes red for a non-code reason is one you stop
    trusting.
    """
    try:
        await assertion
    except APIConnectionError as exc:
        if "RESOURCE_EXHAUSTED" not in str(exc):
            raise
        pytest.skip(
            f"judge quota exhausted for {JUDGE_MODEL}. The free tier meters "
            f"per model per day, so set IRA_JUDGE_MODEL to another flash model "
            f"for a fresh allowance, or enable billing on the key."
        )


# --------------------------------------------------------------------------
# Realtime-only wiring
# --------------------------------------------------------------------------


def test_llm_is_a_gemini_realtime_model() -> None:
    assert isinstance(Assistant().llm, google.realtime.RealtimeModel)


def test_no_separate_stt_or_tts_stage() -> None:
    """A realtime model replaces the STT -> LLM -> TTS pipeline outright."""
    agent = Assistant()
    assert agent.stt is NOT_GIVEN, "a realtime agent must not configure an STT"
    assert agent.tts is NOT_GIVEN, "a realtime agent must not configure a TTS"


def test_configured_model_supports_realtime() -> None:
    assert GEMINI_LIVE_MODEL in LIVE_CAPABLE_MODELS, (
        f"{GEMINI_LIVE_MODEL!r} does not support bidiGenerateContent, so it "
        f"cannot run a realtime session. Use one of: {sorted(LIVE_CAPABLE_MODELS)}"
    )


def test_configured_model_can_speak_first() -> None:
    """src/agent.py calls generate_reply() so IRA opens the conversation.

    Only the native-audio models accept that call; gemini-3.1-flash-live-preview
    rejects it, logs an error, and produces no speech. Pointing
    GEMINI_LIVE_MODEL back at 3.1 must therefore fail here rather than ship an
    agent that silently never greets.
    """
    assert GEMINI_LIVE_MODEL in GREETING_CAPABLE_MODELS, (
        f"{GEMINI_LIVE_MODEL!r} rejects generate_reply(), so the greeting in "
        f"src/agent.py would produce no speech. Use one of: "
        f"{sorted(GREETING_CAPABLE_MODELS)}"
    )


def test_voice_is_kore() -> None:
    """The voice was picked by ear; nothing else in the stack pins it."""
    assert GEMINI_VOICE == "Kore"


def test_model_and_voice_reach_the_plugin() -> None:
    """Guards the constants being wired through, not merely defined.

    Reads a private `_opts` because the plugin exposes no public accessor.
    """
    opts = Assistant().llm._opts
    assert opts.model == GEMINI_LIVE_MODEL
    assert opts.voice == GEMINI_VOICE


# --------------------------------------------------------------------------
# IRA persona
# --------------------------------------------------------------------------


def test_persona_is_ira() -> None:
    instructions = Assistant().instructions
    assert "IRA" in instructions
    assert "boss" in instructions.lower()


def test_persona_does_not_borrow_another_identity() -> None:
    """IRA is her own character, not a renamed FRIDAY.

    She is meant to *behave* like Iron Man's assistant -- composed, terse,
    dryly funny -- without *being* her. Naming a trademarked character in a
    system prompt invites the model to play its own idea of that character
    instead of the one described here, which is exactly the drift this guards
    against.
    """
    instructions = Assistant().instructions.lower()
    for borrowed in ("friday", "iron man", "stark", "jarvis"):
        assert borrowed not in instructions, (
            f"{borrowed!r} in the system prompt makes IRA someone else's character"
        )


def test_persona_explains_the_name_means_nothing() -> None:
    """ "IRA" is not an acronym, so she must not invent an expansion for it.

    Asked "what does IRA stand for?", a model with nothing to go on will
    happily make something up. The prompt has to say outright that there is
    nothing to expand.
    """
    assert "stand for" in Assistant().instructions.lower()


def test_persona_asks_for_a_feminine_voice() -> None:
    """The voice name carries no gender, so the prompt has to say it.

    Google documents only a style descriptor per voice, never a gender, and
    native-audio models pick their own delivery. Asking in the instructions is
    the only lever available in the SDK.
    """
    instructions = Assistant().instructions.lower()
    assert "female" in instructions or "feminine" in instructions
    assert "she" in instructions.split() or "her" in instructions.split()


def test_persona_keeps_the_spoken_output_rules() -> None:
    """Character changes must not drop the rules that keep speech listenable."""
    instructions = Assistant().instructions.lower()
    assert "markdown" in instructions, "must still forbid markdown in speech"
    assert "emoji" in instructions, "must still forbid emoji in speech"


def test_persona_teaches_workflow_reuse_and_durable_notes() -> None:
    instructions = Assistant(
        recent_notes="The boss is preparing a release"
    ).instructions.lower()
    assert "workflow" in instructions
    assert "repeatable" in instructions
    assert "preparing a release" in instructions


def test_turn_taking_target_keeps_dead_air_under_half_a_second() -> None:
    vad = Assistant().llm._opts.realtime_input_config.automatic_activity_detection
    assert vad.silence_duration_ms <= 500


def test_instructions_contain_no_editorial_notes() -> None:
    """Everything in this string is sent to Gemini verbatim.

    Notes to whoever edits the prompt belong in Python comments. A stray
    placeholder or TODO here becomes literal system-prompt text.
    """
    instructions = Assistant().instructions
    for leak in ("PLACEHOLDER", "TODO", "FIXME", "<<<", ">>>"):
        assert leak not in instructions, f"{leak!r} leaked into the system prompt"


# --------------------------------------------------------------------------
# open_app: tool surface
# --------------------------------------------------------------------------


def test_open_app_is_registered_as_a_tool() -> None:
    names = {tool.info.name for tool in Assistant().tools}
    assert "open_app" in names


def test_open_app_description_mentions_opening_an_app() -> None:
    """The description is what the model matches "open Spotify" against."""
    tool = next(t for t in Assistant().tools if t.info.name == "open_app")
    description = (tool.info.description or "").lower()
    assert "open" in description
    assert "app" in description


# --------------------------------------------------------------------------
# open_app: sanitizing the spoken app name
# --------------------------------------------------------------------------


def test_sanitize_keeps_an_ordinary_name() -> None:
    assert _sanitize_app_name("Spotify") == "Spotify"


def test_sanitize_trims_and_collapses_whitespace() -> None:
    assert _sanitize_app_name("  visual   studio  code ") == "visual studio code"


def test_sanitize_strips_newlines() -> None:
    """A newline reaching pyautogui.write() is an extra Enter at the Start menu.

    The name arrives via speech -> model, so it is not trusted input: a
    "notepad\\nshutdown /s" would otherwise submit twice and run the second
    line as its own Start-menu query.
    """
    assert "\n" not in _sanitize_app_name("notepad\nshutdown /s")
    assert "\r" not in _sanitize_app_name("notepad\r\ncalc")


def test_sanitize_strips_control_characters() -> None:
    assert _sanitize_app_name("note\x00pad\x1b[A") == "notepad[A"


def test_sanitize_caps_length() -> None:
    assert len(_sanitize_app_name("a" * 500)) <= 64


def test_sanitize_rejects_an_empty_name() -> None:
    assert _sanitize_app_name("   ") == ""
    assert _sanitize_app_name("\n\n") == ""


# --------------------------------------------------------------------------
# open_app: the PyAutoGUI key sequence
# --------------------------------------------------------------------------


@pytest.fixture
def fake_pyautogui():
    """Stands in for the real module, which needs a desktop to import.

    _launch_via_start_menu imports pyautogui lazily, so replacing the
    sys.modules entry is enough -- and it means these tests never actually
    press keys.
    """
    fake = MagicMock()
    with patch.dict(sys.modules, {"pyautogui": fake}):
        yield fake


def test_launch_presses_win_types_the_name_then_enter(fake_pyautogui) -> None:
    """Order matters: the name must land after the Start menu has focus."""
    with patch("agent.time.sleep"):
        _launch_via_start_menu("Spotify")

    assert fake_pyautogui.mock_calls == [
        call.press("win"),
        call.write("Spotify", interval=0.02),
        call.press("enter"),
    ]


def test_launch_waits_between_keystrokes(fake_pyautogui) -> None:
    """Without the pauses the name is typed before the menu is listening."""
    with patch("agent.time.sleep") as sleep:
        _launch_via_start_menu("Notepad")

    assert sleep.call_count >= 2
    assert all(args[0] > 0 for args, _ in sleep.call_args_list)


async def test_open_app_reports_what_it_did(fake_pyautogui) -> None:
    """It cannot confirm the app launched, so it must not claim that it did."""
    with patch("agent.time.sleep"):
        result = await Assistant().open_app(None, app_name="Spotify")

    assert "Spotify" in result
    fake_pyautogui.press.assert_any_call("win")


async def test_open_app_declines_an_empty_name(fake_pyautogui) -> None:
    """Nothing should be typed at the Start menu when there is no name."""
    with patch("agent.time.sleep"):
        result = await Assistant().open_app(None, app_name="   ")

    assert fake_pyautogui.mock_calls == []
    assert "name" in result.lower()


async def test_open_app_surfaces_a_missing_pyautogui() -> None:
    """The import fails on a headless host, and must not kill the session.

    src/agent.py deploys to LiveKit Cloud, where there is no desktop at all.
    The tool has to come back with something IRA can say instead of raising
    into the session.

    A None entry in sys.modules is how Python signals a blocked import: the
    `import pyautogui` inside the tool raises ImportError against it.
    """
    with patch.dict(sys.modules, {"pyautogui": None}):
        result = await Assistant().open_app(None, app_name="Spotify")

    assert "spotify" in result.lower()


async def test_open_app_survives_a_desktopless_host(fake_pyautogui) -> None:
    """PyAutoGUI raises at call time too, e.g. no DISPLAY on Linux."""
    fake_pyautogui.press.side_effect = RuntimeError("no display")

    with patch("agent.time.sleep"):
        result = await Assistant().open_app(None, app_name="Spotify")

    assert isinstance(result, str)
    assert result, "must return something IRA can say"


# --------------------------------------------------------------------------
# Latency: turn-taking, prewarm, context growth
#
# These three are why the agent felt like it answered a minute late. None of
# them is the model being slow -- they are dead air around it. Structural
# assertions only; measuring real latency needs a live session.
# --------------------------------------------------------------------------


def test_turn_taking_is_tuned_rather_than_left_to_the_default() -> None:
    """Unconfigured, Gemini waits a long, conservative beat before replying.

    That pause lands after every single thing the boss says, which is what
    reads as "the agent is slow" even though generation itself is fast. The
    plugin sends no realtime_input_config unless one is given, so leaving this
    NOT_GIVEN is what the slow behaviour looked like.
    """
    config = Assistant().llm._opts.realtime_input_config
    assert config is not NOT_GIVEN and config is not None, (
        "no realtime_input_config means Gemini's default end-of-turn wait, "
        "which is the dead air that made IRA feel a minute behind"
    )

    vad = config.automatic_activity_detection
    assert vad is not None
    assert not vad.disabled, "server-side VAD is what ends the boss's turn"
    assert vad.silence_duration_ms is not None
    assert vad.silence_duration_ms <= 800, (
        f"{vad.silence_duration_ms}ms of silence before IRA takes her turn is "
        f"most of the delay the boss is hearing"
    )
    assert (
        vad.end_of_speech_sensitivity == genai_types.EndSensitivity.END_SENSITIVITY_HIGH
    )


def test_context_is_compressed_so_later_turns_do_not_slow_down() -> None:
    """Without a sliding window, every turn resends the whole conversation.

    The system prompt alone is already ~1.2k input tokens, so a long session
    degrades steadily -- the twentieth reply is slower than the first for a
    reason that has nothing to do with what was asked.
    """
    compression = Assistant().llm._opts.context_window_compression
    assert compression is not NOT_GIVEN and compression is not None
    assert compression.sliding_window is not None


def test_prewarm_is_registered_on_the_server() -> None:
    """Heavy imports belong at worker warm-up, not in the first turn.

    The console run logged `event loop blocked for 337ms` importing h11 while
    the session was already connecting -- that is 337ms of the greeting's
    latency spent on an import that could have happened before anyone dialled
    in. `setup_fnc` is a settable property on AgentServer, not a decorator.
    """
    assert server.setup_fnc is _prewarm


def test_prewarm_survives_a_missing_dependency() -> None:
    """It runs before any session exists, so raising here costs the whole job."""
    with patch.dict(sys.modules, {"httpx": None, "h11": None}):
        _prewarm(None)


def test_prewarm_includes_anyio_backend_used_by_httpx_cleanup() -> None:
    """The first Gemini connection must not import anyio on the audio loop."""
    assert "anyio" in PREWARM_IMPORTS


def test_console_key_listener_interrupt_is_the_only_suppressed_shutdown_error() -> None:
    listener_args = type(
        "Args",
        (),
        {
            "exc_value": KeyboardInterrupt(),
            "thread": type("T", (), {"name": "Thread-2 (_listen_for_keys)"})(),
        },
    )()
    other_args = type(
        "Args",
        (),
        {
            "exc_value": RuntimeError("broken"),
            "thread": type("T", (), {"name": "worker"})(),
        },
    )()

    assert _is_console_key_listener_interrupt(listener_args)
    assert not _is_console_key_listener_interrupt(other_args)


# --------------------------------------------------------------------------
# Browser and memory tools
#
# Every one of these wraps a blocking library in asyncio.to_thread and catches
# its own exceptions. Both matter for the same reason: a tool that raises into
# a realtime session drops the turn, and IRA goes silent with no way to explain
# herself. The fakes here never start Chrome -- see tests/test_browser_and_memory.py.
# --------------------------------------------------------------------------


class FakeBrowser:
    """Stands in for BrowserController without launching anything."""

    def __init__(self) -> None:
        self.visited: list[str] = []
        self.closed = False

    def navigate(self, url: str) -> str:
        self.visited.append(url)
        return "Example Domain. 3 things to act on."

    def open_spotify(self, browser: str | None = None) -> str:
        return f"Spotify opened in {browser or 'chrome'}."

    def read_page(self) -> str:
        return "Some page text."

    def list_elements(self) -> list[PageElement]:
        return [PageElement(1, "link", "Sign in"), PageElement(2, "textbox", "Search")]

    def click(self, index: int) -> str:
        return f"Clicked element {index}."

    def type_text(self, index: int, text: str, *, submit: bool = False) -> str:
        return f"Typed {text} into {index}."

    def back(self) -> str:
        return "Previous page."

    def close(self) -> str:
        self.closed = True
        return "Browser closed."

    def spotify_control(self, action: str, seconds: int = 10) -> str:
        return f"Spotify {action} requested ({seconds})."


BROWSER_TOOLS = {
    "browse_to",
    "read_page",
    "list_page_elements",
    "click_element",
    "type_into_element",
    "go_back",
    "close_browser",
    "spotify_control",
    "spotify_play_playlist",
    "open_spotify",
}
MEMORY_TOOLS = {"remember", "recall", "forget"}


def test_browser_tools_are_registered() -> None:
    names = {tool.info.name for tool in Assistant().tools}
    assert names >= BROWSER_TOOLS, f"missing: {sorted(BROWSER_TOOLS - names)}"


def test_memory_tools_are_registered() -> None:
    names = {tool.info.name for tool in Assistant().tools}
    assert names >= MEMORY_TOOLS, f"missing: {sorted(MEMORY_TOOLS - names)}"


async def test_browse_to_reports_the_page_it_landed_on() -> None:
    browser = FakeBrowser()
    result = await Assistant(browser=browser).browse_to(None, url="example.com")

    assert browser.visited == ["example.com"]
    assert "Example Domain" in result


async def test_spotify_control_is_available_as_a_voice_tool() -> None:
    result = await Assistant(browser=FakeBrowser()).spotify_control(
        None, action="pause"
    )
    assert "spotify pause" in result.lower()


async def test_spotify_playlist_tool_is_available_as_a_voice_tool() -> None:
    class PlaylistBrowser(FakeBrowser):
        def spotify_play_playlist(self, name: str) -> str:
            return f"Playing Spotify playlist {name}."

    result = await Assistant(browser=PlaylistBrowser()).spotify_play_playlist(
        None, name="My Funk Songs"
    )
    assert "my funk songs" in result.lower()


def test_status_reporter_ignores_interim_transcript_spam() -> None:
    callbacks = {}

    class FakeSession:
        def on(self, name, callback):
            callbacks[name] = callback

    lines: list[str] = []
    from agent import _attach_status_reporter

    _attach_status_reporter(FakeSession(), SessionStatusReporter(sink=lines.append))
    callbacks["user_input_transcribed"](type("Event", (), {"is_final": False})())
    callbacks["user_input_transcribed"](type("Event", (), {"is_final": True})())

    assert len(lines) == 1
    assert "final transcript" in lines[0]


async def test_open_spotify_is_available_as_a_voice_tool() -> None:
    result = await Assistant(browser=FakeBrowser()).open_spotify(None, browser="brave")
    assert "brave" in result.lower()


def test_workflow_tools_are_registered() -> None:
    names = {tool.info.name for tool in Assistant().tools}
    assert {
        "save_workflow",
        "list_workflows",
        "predict_workflow",
        "run_workflow",
        "delete_workflow",
        "adapt_task",
        "open_workflows_folder",
        "capture_screen",
        "move_mouse",
        "click_mouse",
        "type_on_desktop",
    } <= names


async def test_workflow_tools_persist_and_retrieve_markdown(tmp_path) -> None:
    agent = Assistant(workflows=WorkflowStore(tmp_path))
    await agent.save_workflow(
        None,
        name="Morning setup",
        goal="Prepare the work session",
        steps=["Open Spotify", "Start focus playlist"],
    )
    result = await agent.run_workflow(None, name="morning setup")
    assert "Prepare the work session" in result
    assert "Start focus playlist" in result


async def test_predict_workflow_explains_the_selected_plan(tmp_path) -> None:
    store = WorkflowStore(tmp_path)
    store.save(
        "Play focus music",
        "Start the saved focus playlist in Spotify",
        ["open_spotify: browser=chrome", "spotify_control: action=play"],
        allowed_tools=["open_spotify", "spotify_control"],
        verification=["Playback is active"],
    )

    result = await Assistant(workflows=store).predict_workflow(
        None, request="start my focus playlist in Spotify"
    )

    assert "Play focus music" in result
    assert "open_spotify" in result
    assert "Playback is active" in result


async def test_run_workflow_accepts_a_natural_language_request(tmp_path) -> None:
    store = WorkflowStore(tmp_path)
    store.save(
        "Play focus music",
        "Start the saved focus playlist in Spotify",
        ["open_spotify: browser=chrome", "spotify_control: action=play"],
        allowed_tools=["open_spotify", "spotify_control"],
        verification=["Playback is active"],
    )

    result = await Assistant(browser=FakeBrowser(), workflows=store).run_workflow(
        None, "start my focus playlist in Spotify"
    )

    assert "Play focus music" in result
    assert "completed" in result.lower()


async def test_detailed_workflow_executes_only_declared_actions(tmp_path) -> None:
    lines: list[str] = []
    store = WorkflowStore(tmp_path)
    store.save(
        "Play focus",
        "Play focus music",
        ["open_spotify: browser=chrome", "spotify_control: action=play"],
        allowed_tools=["open_spotify", "spotify_control"],
        verification=["Playback is active"],
    )

    result = await Assistant(
        browser=FakeBrowser(),
        workflows=store,
        reporter=SessionStatusReporter(sink=lines.append),
    ).run_workflow(None, "play focus")

    assert "completed" in result.lower()
    assert any("DELEGATING" in line for line in lines)
    assert any("DONE" in line for line in lines)


async def test_executable_workflow_requires_allowlist_and_verification(
    tmp_path,
) -> None:
    store = WorkflowStore(tmp_path)
    store.save("Unsafe", "Unsafe", ["open_app: app_name=Notepad"])

    result = await Assistant(workflows=store).run_workflow(None, "unsafe")

    assert "allowed_tools" in result


async def test_open_workflows_folder_uses_the_workflow_store(tmp_path) -> None:
    opened = []
    agent = Assistant(
        workflows=WorkflowStore(tmp_path / "workflows", opener=opened.append)
    )
    result = await agent.open_workflows_folder(None)
    assert opened == [agent._workflows.root]
    assert "opened" in result.lower()


async def test_save_note_is_durable_and_seeded(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    agent = Assistant(memory=store)
    await agent.save_note(None, note="The boss prefers concise answers")
    reopened = MemoryStore(tmp_path / "memory.db")
    assert "concise answers" in (
        await Assistant(memory=reopened).recall(None, query="concise")
    )


async def test_preference_tool_persists_and_lists_preferences(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    agent = Assistant(memory=store)
    result = await agent.remember_preference(
        None, preference="The boss prefers concise answers"
    )
    assert "preference" in result.lower()
    listed = await agent.list_preferences(None)
    assert "concise answers" in listed


def test_preference_tools_are_registered() -> None:
    names = {tool.info.name for tool in Assistant().tools}
    assert {"remember_preference", "list_preferences"} <= names


def test_preferences_are_in_startup_instructions(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    store.remember("The boss prefers concise answers", kind="preference")
    agent = Assistant(memory=store, preferences="The boss prefers concise answers")
    assert "concise answers" in agent.instructions


async def test_adapt_task_saves_a_reusable_workflow(tmp_path) -> None:
    agent = Assistant(workflows=WorkflowStore(tmp_path))
    result = await agent.adapt_task(
        None,
        task="Prepare my focus session",
        steps=["Open Spotify", "Start the focus playlist"],
    )
    assert "saved" in result.lower()
    assert agent._workflows.get("prepare my focus session") is not None


async def test_list_page_elements_speaks_them_numbered() -> None:
    """The model acts by number, so the numbering has to survive into speech."""
    result = await Assistant(browser=FakeBrowser()).list_page_elements(None)

    assert "1. link Sign in" in result
    assert "2. textbox Search" in result


async def test_browser_failures_come_back_as_speech() -> None:
    class Broken(FakeBrowser):
        def navigate(self, url: str) -> str:
            raise RuntimeError("net::ERR_NAME_NOT_RESOLVED")

    result = await Assistant(browser=Broken()).browse_to(None, url="nope.invalid")

    assert isinstance(result, str) and result
    assert "err_name_not_resolved" not in result.lower(), (
        "raw Chrome error codes are not something IRA can say out loud"
    )


async def test_an_unavailable_browser_explains_itself() -> None:
    """No Chrome on the host is not retryable, and the advice differs."""

    class NoChrome(FakeBrowser):
        def navigate(self, url: str) -> str:
            raise BrowserUnavailableError("cannot find Chrome binary")

    result = await Assistant(browser=NoChrome()).browse_to(None, url="example.com")

    assert "browser" in result.lower()


@pytest.fixture
def memory(tmp_path):
    store = MemoryStore(tmp_path / "agent_memory.db")
    yield store
    store.close()


async def test_remember_then_recall_through_the_tools(memory) -> None:
    agent = Assistant(memory=memory)
    await agent.remember(None, fact="The boss takes his coffee black")

    assert "coffee" in (await agent.recall(None, query="coffee")).lower()


async def test_recall_says_so_when_it_knows_nothing(memory) -> None:
    """An empty answer would leave the model free to invent a memory."""
    result = await Assistant(memory=memory).recall(None, query="anything at all")

    assert isinstance(result, str) and result


async def test_memory_failures_come_back_as_speech() -> None:
    class Broken:
        def remember(self, content: str, *, kind: str = "fact") -> str:
            raise RuntimeError("database is locked")

    result = await Assistant(memory=Broken()).remember(None, fact="something")

    assert isinstance(result, str) and result


# --------------------------------------------------------------------------
# Seeding memory into the prompt
# --------------------------------------------------------------------------


def test_seed_facts_reach_the_instructions() -> None:
    instructions = Assistant(seed_facts="The boss dislikes small talk").instructions
    assert "The boss dislikes small talk" in instructions


def test_no_seed_facts_leaves_no_empty_heading() -> None:
    """An empty "what you already know" section invites the model to fill it."""
    assert "already know" not in Assistant().instructions.lower()


def test_format_seed_facts_renders_what_is_stored(memory) -> None:
    memory.remember("The boss lives in Hyderabad")
    assert "Hyderabad" in _format_seed_facts(memory)


def test_format_seed_facts_survives_an_unreadable_store() -> None:
    """Memory being unavailable must not stop the session from starting."""

    class Broken:
        def seed(self):
            raise RuntimeError("no such table: memories")

    assert _format_seed_facts(Broken()) == ""


# --------------------------------------------------------------------------
# Turn-level evals (live API)
#
# Every clause in an `intent=` is a criterion the judge enforces, so write the
# requirement and nothing else. Decorative glosses and word counts -- "in the
# first few words", "it is simply her name" -- read as hard criteria to a
# literal judge and fail replies that were correct. Both examples are real:
# each cost a red suite here before the intent was trimmed to the actual rule.
# --------------------------------------------------------------------------


@requires_live_api
async def test_replies_in_character_as_ira() -> None:
    """The native-audio model accepts generate_reply(), so evals work at all.

    Judging needs a plain text LLM: `session.llm` here is the realtime model,
    and `judge()` reads `llm.model`, which a RealtimeModel leaves as None.

    Deliberately not judged on "leads with the answer": a greeting has no
    substance to lead with, so "Hello, boss. What can I do for you?" is the
    correct reply here and reads as a preamble to a judge asked to look for
    one. That trait is checked on a turn that actually has an answer, below.
    """
    async with AgentSession() as session:
        await session.start(Assistant())
        result = await session.run(user_input="Hello?")

    await _judged(
        result.expect.next_event()
        .is_message(role="assistant")
        .judge(
            _judge_llm(),
            intent=(
                "Greets the user in character as IRA: brief, composed, and "
                "addressing them as boss. Plain spoken prose with no markdown, "
                "lists, or emoji."
            ),
        )
    )


@requires_live_api
async def test_leads_with_the_answer() -> None:
    """The persona's headline trait, on a turn where it is observable.

    A question with a one-word answer is the sharpest test of it: a model that
    has ignored the instruction reaches for "Sure, let me think" or restates
    the question before answering, and both are visible here in a way they
    would not be in a longer reply.

    This failed roughly two runs in three until the persona instruction grew a
    worked example ("Twelve percent, boss" over "You have twelve percent
    battery remaining"). Before that she answered "The capital of France is
    Paris, boss" -- in character, but restating the question first, which is
    the thing the instruction names. Telling the model the rule was not enough;
    showing it one was. The example deliberately uses a different question than
    this test asks, so what is measured is the pattern generalizing rather than
    the answer being memorized.

    The intent asks only that she begin with the answer. An earlier version
    asked for it "in the first few words", which is not a rule the persona
    makes and left the judge counting words on replies like "That would be
    Paris, boss" -- the source of an occasional failure on a reply that broke
    no rule.
    """
    async with AgentSession() as session:
        await session.start(Assistant())
        result = await session.run(user_input="What's the capital of France?")

    await _judged(
        result.expect.next_event()
        .is_message(role="assistant")
        .judge(
            _judge_llm(),
            intent=(
                "Begins with the answer, Paris. Must NOT begin with filler "
                "such as 'sure', 'of course', or 'let me check', and must NOT "
                "restate the question before giving the answer."
            ),
        )
    )


@requires_live_api
async def test_does_not_invent_a_meaning_for_her_name() -> None:
    """ "IRA" is not an acronym, and she must not pretend otherwise.

    This is the persona's most likely silent failure: asked what her name
    stands for, a model reaches for a plausible expansion ("Intelligent
    Responsive Assistant") and delivers it with total confidence. The prompt
    says there is nothing to expand; this checks she believes it.
    """
    async with AgentSession() as session:
        await session.start(Assistant())
        result = await session.run(user_input="What does IRA stand for?")

    await _judged(
        result.expect.next_event()
        .is_message(role="assistant")
        .judge(
            _judge_llm(),
            intent=(
                "Denies that the name is an acronym or stands for anything. "
                "Must NOT offer an expansion or acronym of any kind, and must "
                "not claim the meaning is secret, forgotten, or unknown to her."
            ),
        )
    )


@requires_live_api
async def test_calls_open_app_when_asked_to_open_something() -> None:
    """The whole point of the tool: 'open X' has to reach open_app.

    The tool is mocked, and must stay mocked: unmocked, this test presses Win
    and types into whatever window is focused on the machine running pytest.

    `contains_function_call` rather than `next_event`: the model narrates
    ("Opening Notepad, boss.") before it calls the tool, so the call is not the
    first event in the run.

    The app name is compared case-insensitively rather than passed to
    `arguments=`, which compares with `!=`. Nothing in the persona promises the
    model will echo the user's capitalization, so an exact match makes this
    fail on "notepad" -- a spelling difference, not the behavior under test.
    """

    async def fake_open_app(app_name: str) -> str:
        return f"Asked Windows to open {app_name}."

    with mock_tools(Assistant, {"open_app": fake_open_app}):
        async with AgentSession() as session:
            await session.start(Assistant())
            result = await session.run(user_input="Open Notepad for me.")

    call = result.expect.contains_function_call(name="open_app").event()
    arguments = json.loads(call.item.arguments)
    assert arguments["app_name"].strip().lower() == "notepad"


# ---------------------------------------------------------------------------
# Desktop dashboard wiring (sub-project B)
#
# The dashboard reaches IRA two ways: typed commands arrive as ordinary user
# turns, and her lifecycle states go back out over a data topic so the orb can
# follow them. Both are agent behaviour, so both get tests.
# ---------------------------------------------------------------------------


def test_text_input_is_enabled_so_the_command_bar_works() -> None:
    """Typing in the dashboard must reach her as a real turn.

    `RoomOptions` leaves `text_input` NOT_GIVEN by default, which registers no
    handler for the lk.chat topic -- the command bar would publish into
    silence, with no error anywhere to explain it.
    """
    options = _room_options()
    assert options.text_input is not NOT_GIVEN
    assert options.text_input is not False


def test_noise_cancellation_survives_the_text_input_change() -> None:
    # Guard against the obvious regression: rebuilding RoomOptions to add
    # text input and dropping the audio enhancement while doing it.
    options = _room_options()
    assert options.audio_input is not NOT_GIVEN
    assert options.audio_input.noise_cancellation is not None


class _FakeParticipant:
    def __init__(self) -> None:
        self.published: list[tuple[bytes, str, bool]] = []

    async def publish_data(self, payload, *, topic="", reliable=True, **_kwargs):
        self.published.append((payload, topic, reliable))


class _FakeRoom:
    def __init__(self, participant=None) -> None:
        self.local_participant = participant or _FakeParticipant()

    def isconnected(self) -> bool:
        return True


class _ConnectableRoom:
    """Raises on `local_participant` until connected, as `rtc.Room` does."""

    def __init__(self) -> None:
        self.connected = False
        self._participant = _FakeParticipant()

    def isconnected(self) -> bool:
        return self.connected

    @property
    def local_participant(self):
        if not self.connected:
            raise Exception("cannot access local participant before connecting")
        return self._participant


async def test_status_publisher_sends_json_on_the_status_topic() -> None:
    """A fake room, never a real one.

    Same rule the browser and memory suites follow: these tests must not open
    a session, a window, or a socket.
    """
    room = _FakeRoom()
    publish = _room_status_publisher(room)

    publish({"run_id": "abc", "state": "DELEGATING", "detail": "open_app", "at": 1.0})
    # Drain rather than yield once: how many loop iterations the publish
    # takes is an implementation detail this test should not encode.
    await asyncio.sleep(0.01)

    assert len(room.local_participant.published) == 1
    payload, topic, reliable = room.local_participant.published[0]
    assert topic == STATUS_TOPIC
    # Reliable: the dashboard pairs each tool's DELEGATING with its DONE, and
    # nothing corrects a lost DONE -- the orb would show a finished tool as
    # running until its thirty-second safety net expired.
    assert reliable is True

    event = json.loads(payload.decode())
    assert event["state"] == "DELEGATING"
    assert event["detail"] == "open_app"


async def test_status_publisher_never_raises_into_the_session() -> None:
    """A dead room must cost the orb, not the call."""

    class _BrokenRoom:
        class local_participant:  # noqa: N801
            @staticmethod
            async def publish_data(*_args, **_kwargs):
                raise RuntimeError("room closed")

        def isconnected(self) -> bool:
            return True

    publish = _room_status_publisher(_BrokenRoom())
    publish({"run_id": "abc", "state": "ERROR", "detail": "", "at": 1.0})
    await asyncio.sleep(0.01)


async def test_reporter_wired_to_a_room_publishes_its_states() -> None:
    """End to end through the real reporter, with a fake room.

    This is the path the orb depends on: SessionStatusReporter -> observer ->
    data topic. Testing the publisher alone would not catch the reporter and
    the publisher being wired up with mismatched shapes.
    """
    room = _FakeRoom()
    reporter = SessionStatusReporter(
        run_id="run-1", sink=lambda _: None, observer=_room_status_publisher(room)
    )

    reporter.emit("SPEAKING")
    await asyncio.sleep(0.01)

    event = json.loads(room.local_participant.published[0][0].decode())
    assert event["state"] == "SPEAKING"
    assert event["run_id"] == "run-1"


def _tool_events(callbacks):
    """Drive the reporter's tool callback with the SDK's real event shapes."""
    from types import SimpleNamespace

    def started(call_id: str, name: str):
        return SimpleNamespace(
            update=SimpleNamespace(
                type="tool_call_started",
                function_call=SimpleNamespace(call_id=call_id, name=name),
            )
        )

    def ended(call_id: str, status: str):
        return SimpleNamespace(
            update=SimpleNamespace(
                type="tool_call_ended", id=call_id, call_id=call_id, status=status
            )
        )

    return started, ended


def test_status_reporter_names_the_tool_that_started() -> None:
    """The dashboard's LEARN cue lights for memory-writing tools by name.

    The reporter used to emit the literal string "tool call", which discarded
    the one fact the cue needs -- `ToolCallStarted.function_call.name` was
    right there on the event.
    """
    callbacks = {}

    class FakeSession:
        def on(self, name, callback):
            callbacks[name] = callback

    events: list[dict] = []
    from agent import _attach_status_reporter

    _attach_status_reporter(
        FakeSession(),
        SessionStatusReporter(sink=lambda _: None, observer=events.append),
    )
    started, _ = _tool_events(callbacks)
    callbacks["tool_execution_updated"](started("call-1", "remember"))

    assert events[-1]["state"] == "DELEGATING"
    assert events[-1]["detail"] == "remember"


def test_status_reporter_names_the_tool_that_ended() -> None:
    """The end event carries only a call id, so the name is remembered from
    the start event rather than lost."""
    callbacks = {}

    class FakeSession:
        def on(self, name, callback):
            callbacks[name] = callback

    events: list[dict] = []
    from agent import _attach_status_reporter

    _attach_status_reporter(
        FakeSession(),
        SessionStatusReporter(sink=lambda _: None, observer=events.append),
    )
    started, ended = _tool_events(callbacks)
    callbacks["tool_execution_updated"](started("call-1", "open_app"))
    callbacks["tool_execution_updated"](ended("call-1", "done"))
    callbacks["tool_execution_updated"](started("call-2", "read_page"))
    callbacks["tool_execution_updated"](ended("call-2", "error"))

    assert (events[1]["state"], events[1]["detail"]) == ("DONE", "open_app")
    assert (events[3]["state"], events[3]["detail"]) == ("ERROR", "read_page")


def test_status_reporter_survives_an_end_without_a_start() -> None:
    # A reconnect can deliver an end for a call this session never saw start.
    callbacks = {}

    class FakeSession:
        def on(self, name, callback):
            callbacks[name] = callback

    events: list[dict] = []
    from agent import _attach_status_reporter

    _attach_status_reporter(
        FakeSession(),
        SessionStatusReporter(sink=lambda _: None, observer=events.append),
    )
    _, ended = _tool_events(callbacks)
    callbacks["tool_execution_updated"](ended("never-started", "done"))

    assert events[-1]["state"] == "DONE"
    assert events[-1]["detail"] == "tool call"


async def test_status_publisher_holds_events_until_the_room_connects() -> None:
    """Found running the real app end to end.

    The entrypoint emits "session starting" before `ctx.connect()`, and
    `room.local_participant` raises until the room is connected -- so that
    first state was lost, with a traceback in her log on every session start.
    Events are now held while disconnected and flushed, in order, once
    connected.
    """
    room = _ConnectableRoom()
    publish = _room_status_publisher(room)

    publish(
        {"run_id": "a", "state": "LISTENING", "detail": "session starting", "at": 1.0}
    )
    await asyncio.sleep(0.01)
    assert room._participant.published == []

    room.connected = True
    publish({"run_id": "a", "state": "GENERATING", "detail": "", "at": 2.0})
    await asyncio.sleep(0.01)

    states = [json.loads(p)["state"] for p, _, _ in room._participant.published]
    assert states == ["LISTENING", "GENERATING"]


async def test_status_publisher_bounds_what_it_holds() -> None:
    """A room slow to connect must not grow the backlog without limit.

    Oldest go first: after a long connect the orb needs the latest state, not
    the first.
    """
    room = _ConnectableRoom()
    publish = _room_status_publisher(room)
    for n in range(STATUS_BACKLOG * 3):
        publish({"run_id": "a", "state": "RUNNING", "detail": str(n), "at": float(n)})

    room.connected = True
    publish({"run_id": "a", "state": "DONE", "detail": "last", "at": 999.0})
    await asyncio.sleep(0.01)

    details = [json.loads(p)["detail"] for p, _, _ in room._participant.published]
    assert len(details) == STATUS_BACKLOG + 1
    assert details[0] == str(STATUS_BACKLOG * 2)
    assert details[-1] == "last"


async def test_status_publisher_keeps_order_when_a_send_is_slow() -> None:
    """DONE must never overtake its DELEGATING.

    Each event used to go out as its own task, so a slow first send let the
    second one land first.
    """

    class _SlowFirstParticipant(_FakeParticipant):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        async def publish_data(self, payload, **kwargs):
            self.calls += 1
            if self.calls == 1:
                await asyncio.sleep(0.02)
            await super().publish_data(payload, **kwargs)

    room = _FakeRoom(_SlowFirstParticipant())
    publish = _room_status_publisher(room)

    publish({"run_id": "a", "state": "DELEGATING", "detail": "open_app", "at": 1.0})
    publish({"run_id": "a", "state": "DONE", "detail": "open_app", "at": 2.0})
    await asyncio.sleep(0.05)

    states = [json.loads(p)["state"] for p, _, _ in room.local_participant.published]
    assert states == ["DELEGATING", "DONE"]
