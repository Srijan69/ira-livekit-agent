"""Offline contracts for Gemini model selection and safe realtime failover."""

import asyncio
import time
from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from google.genai import types
from livekit.agents import NOT_GIVEN, llm
from livekit.plugins import google

from realtime_model import build_realtime_model


class FakeSession(llm.RealtimeSession):
    """A realtime transport with manually controlled provider failures."""

    def __init__(self, model):
        super().__init__(model)
        self._chat_ctx = llm.ChatContext.empty()
        self._tools = llm.ToolContext.empty()
        self.instructions = None
        self.tool_choice = NOT_GIVEN
        self.closed = False
        self.replies = []

    @property
    def chat_ctx(self):
        return self._chat_ctx

    @property
    def tools(self):
        return self._tools

    async def update_instructions(self, instructions):
        self.instructions = instructions

    async def update_chat_ctx(self, chat_ctx):
        self._chat_ctx = chat_ctx.copy()

    async def update_tools(self, tools):
        self._tools = llm.ToolContext(tools)

    def update_options(self, *, tool_choice=NOT_GIVEN):
        self.tool_choice = tool_choice

    def generate_reply(self, **kwargs):
        future = asyncio.get_running_loop().create_future()
        self.replies.append((kwargs, future))
        return future

    def push_audio(self, frame):
        pass

    def push_video(self, frame):
        pass

    def commit_audio(self):
        pass

    def clear_audio(self):
        pass

    def interrupt(self):
        pass

    def truncate(self, **kwargs):
        pass

    async def aclose(self):
        self.closed = True
        for _, future in self.replies:
            if not future.done():
                future.cancel()

    def fail(self, *, recoverable=False):
        self.emit(
            "error",
            llm.RealtimeModelError(
                timestamp=time.time(),
                label="fake-gemini",
                error=ConnectionError("provider disconnected"),
                recoverable=recoverable,
            ),
        )


@pytest.fixture
def transports(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "offline-test-key")
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.delenv("IRA_GEMINI_PRIMARY_MODEL", raising=False)
    monkeypatch.delenv("IRA_GEMINI_FALLBACK_MODEL", raising=False)
    sessions = defaultdict(list)

    def create_session(model, **kwargs):
        session = FakeSession(model)
        sessions[model.model].append(session)
        return session

    monkeypatch.setattr(google.realtime.RealtimeModel, "session", create_session)
    return sessions


def build_model():
    return build_realtime_model(
        voice="Kore",
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(
                silence_duration_ms=300,
            )
        ),
        context_window_compression=types.ContextWindowCompressionConfig(
            sliding_window=types.SlidingWindow(target_tokens=10000)
        ),
    )


def test_models_use_developer_api_native_audio_and_matching_voice(transports):
    model = build_model()
    assert [child.model for child in model.models] == [
        "gemini-3.8-live",
        "gemini-3.8-live-extended-thinking",
    ]
    for child in model.models:
        assert child.capabilities.audio_output
        assert child.capabilities.mutable_chat_context
        assert child._opts.vertexai is False
        assert child._opts.voice == "Kore"
        assert child._opts.response_modalities == [types.Modality.AUDIO]
        assert child._opts.temperature == 0.6
        assert (
            child._opts.realtime_input_config.automatic_activity_detection.silence_duration_ms
            == 300
        )
        assert (
            child._opts.context_window_compression.sliding_window.target_tokens == 10000
        )
    assert model.models[0]._opts.thinking_config is NOT_GIVEN
    assert model.models[1]._opts.thinking_config.thinking_level is not None


def test_environment_can_select_another_native_audio_model(transports, monkeypatch):
    monkeypatch.setenv(
        "IRA_GEMINI_FALLBACK_MODEL", "gemini-2.5-flash-native-audio-preview-12-2025"
    )
    assert (
        build_model().models[1].model == "gemini-2.5-flash-native-audio-preview-12-2025"
    )


@pytest.mark.parametrize(
    "value",
    ["gemini-2.5-flash", "gemini-3.8-live"],
)
def test_invalid_or_duplicate_fallback_is_rejected(transports, monkeypatch, value):
    monkeypatch.setenv("IRA_GEMINI_FALLBACK_MODEL", value)
    with pytest.raises(ValueError, match=r"fallback|native"):
        build_model()


def test_gemini_extended_thinking_as_fallback(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "offline-test-key")
    monkeypatch.setenv("IRA_GEMINI_FALLBACK_MODEL", "gemini-3.8-live-extended-thinking")
    monkeypatch.setenv("IRA_THINKING_LEVEL", "high")
    model = build_model()
    assert model.models[0].model == "gemini-3.8-live"
    assert model.models[0]._opts.thinking_config is NOT_GIVEN

    assert model.models[1].model == "gemini-3.8-live-extended-thinking"
    assert (
        model.models[1]._opts.thinking_config.thinking_level == types.ThinkingLevel.HIGH
    )
    assert model.models[1]._opts.tool_behavior == types.Behavior.NON_BLOCKING


@llm.function_tool
async def record_task(title: str) -> str:
    """Record a task."""
    raise AssertionError("failover must never execute tools")


async def test_provider_failure_preserves_state_closes_old_session_and_does_not_replay(
    transports,
):
    model = build_model()
    session = model.session()
    old = transports["gemini-3.8-live"][0]
    context = llm.ChatContext.empty()
    context.add_message(role="user", content="Remember the shopping list")
    await session._update_session(
        instructions="You are IRA; address the user as boss.",
        chat_ctx=context,
        tools=[record_task],
    )
    session.update_options(tool_choice="auto")
    # Match the binding used by AgentActivity, so this really exercises the
    # adapter's no-regeneration policy during an interrupted tool-bearing turn.
    agent_session = SimpleNamespace(
        agent_state="thinking",
        current_agent=SimpleNamespace(chat_ctx=context),
        interrupt=AsyncMock(),
        generate_reply=Mock(),
    )
    session._agent_session = agent_session
    changed = asyncio.Event()
    model_changes = []
    model.on("model_changed", model_changes.append)
    session.on("session_reconnected", lambda event: changed.set())
    forwarded = []
    session.on("generation_created", forwarded.append)
    try:
        old.fail()
        await asyncio.wait_for(changed.wait(), 1)
        fallback = transports["gemini-3.8-live-extended-thinking"][0]
        assert old.closed
        assert fallback.instructions == "You are IRA; address the user as boss."
        assert fallback.chat_ctx.items == context.items
        assert "record_task" in fallback.tools.function_tools
        assert fallback.tool_choice == "auto"
        assert fallback.replies == []
        agent_session.generate_reply.assert_not_called()
        assert [(event.previous_model, event.model) for event in model_changes] == [
            ("gemini-3.8-live", "gemini-3.8-live-extended-thinking")
        ]
        old.emit("generation_created", object())
        assert forwarded == []
        assert not fallback.closed
    finally:
        await session.aclose()
    assert fallback.closed


async def test_generation_timeout_switches_model_without_reissuing_tools(transports):
    model = build_model()
    session = model.session()
    old = transports["gemini-3.8-live"][0]
    changed = asyncio.Event()
    session.on("session_reconnected", lambda event: changed.set())
    try:
        reply = session.generate_reply(instructions="Open the browser")
        old.replies[0][1].set_exception(llm.RealtimeError("generation timed out"))
        with pytest.raises(llm.RealtimeError):
            await reply
        await asyncio.wait_for(changed.wait(), 1)
        fallback = transports["gemini-3.8-live-extended-thinking"][0]
        assert old.closed
        assert fallback.replies == []
        next_reply = session.generate_reply(
            instructions="Say hello", tool_choice="none"
        )
        assert fallback.replies[0][0]["instructions"] == "Say hello"
        assert fallback.replies[0][0]["tool_choice"] == "none"
        next_reply.cancel()
    finally:
        await session.aclose()


async def test_cancelled_generation_does_not_switch_models(transports):
    session = build_model().session()
    try:
        future = session.generate_reply()
        future.cancel()
        await asyncio.sleep(0)
        assert transports["gemini-3.8-live-extended-thinking"] == []
    finally:
        await session.aclose()


async def test_synchronous_generation_failure_also_switches_models(transports):
    session = build_model().session()
    old = transports["gemini-3.8-live"][0]
    old.generate_reply = Mock(side_effect=ConnectionError("provider connection lost"))
    changed = asyncio.Event()
    session.on("session_reconnected", lambda event: changed.set())
    try:
        with pytest.raises(ConnectionError):
            await session.generate_reply()
        await asyncio.wait_for(changed.wait(), 1)
        assert old.closed
    finally:
        await session.aclose()


async def test_closing_before_failure_callback_does_not_open_fallback(transports):
    session = build_model().session()
    reply = session.generate_reply()
    reply.set_exception(llm.RealtimeError("provider failed during shutdown"))
    await session.aclose()
    with pytest.raises(llm.RealtimeError):
        await reply
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert transports["gemini-3.8-live-extended-thinking"] == []


async def test_both_models_failing_surfaces_terminal_error(transports):
    session = build_model().session()
    changed = asyncio.Event()
    errors = []
    session.on("session_reconnected", lambda event: changed.set())
    session.on("error", errors.append)
    try:
        transports["gemini-3.8-live"][0].fail()
        await asyncio.wait_for(changed.wait(), 1)
        transports["gemini-3.8-live-extended-thinking"][0].fail()
        assert [error.recoverable for error in errors] == [True, False]
    finally:
        await session.aclose()
