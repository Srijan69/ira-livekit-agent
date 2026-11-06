"""Gemini native-audio selection and state-preserving, non-replaying failover.

Model configuration: https://docs.livekit.io/agents/models/realtime/plugins/gemini/
LiveKit's RealtimeModelFallbackAdapter owns swaps, context replay, and cleanup.
The small session extension turns generation-future errors into the terminal
error events that adapter expects (the Google plugin does not emit them).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

from google.genai import types
from livekit.agents import NOT_GIVEN, APIError, llm
from livekit.agents.llm.realtime_fallback_adapter import _FallbackRealtimeSession
from livekit.agents.types import NotGivenOr
from livekit.agents.utils import images
from livekit.plugins import google

logger = logging.getLogger(__name__)

GEMINI_LIVE_MODEL = "gemini-3.8-live"
GEMINI_FALLBACK_MODEL = "gemini-3.8-live-extended-thinking"
GEMINI_EXTENDED_THINKING_MODEL = "gemini-3.8-live-extended-thinking"
_NATIVE_AUDIO_MODELS = frozenset(
    {
        GEMINI_LIVE_MODEL,
        GEMINI_FALLBACK_MODEL,
        GEMINI_EXTENDED_THINKING_MODEL,
        "gemini-3.8-flash-live",
        "gemini-3.8-flash-iive",
        "gemini-3.8flash-extended-thinking",
        "gemini-3.8-flash-extended-thinking",
        "gemini-3.1-flash-live-preview",
        "gemini-2.5-flash-native-audio-latest",
        "gemini-2.5-flash-native-audio-preview-12-2025",
    }
)


def _normalize_model_name(name: str) -> str:
    cleaned = name.strip().lower()
    if cleaned in (
        "gemini-3.8-flash-live",
        "gemini-3.8-flash-iive",
        "gemini 3.8-flash-iive",
        "gemini-3.8-live",
    ):
        return GEMINI_LIVE_MODEL
    if cleaned in (
        "gemini-3.8flash-extended-thinking",
        "gemini-3.8-flash-extended-thinking",
        "gemini 3.8flash-extended-thinking",
        "gemini-3.8-live-extended-thinking",
    ):
        return GEMINI_EXTENDED_THINKING_MODEL
    return cleaned


_PROVIDER_ERRORS = (llm.RealtimeError, APIError, TimeoutError, OSError)


@dataclass(frozen=True)
class ModelChangedEvent:
    previous_model: str
    model: str


@dataclass(frozen=True)
class ProviderActivityEvent:
    generation: int
    kind: str
    payload: Any = None


class _GenerationAwareSession(_FallbackRealtimeSession):
    """Compatibility shim for the fallback adapter in livekit-agents 1.8.8.

    This is the only SDK-private extension: keep the pinned version floor and the
    offline timeout/cancellation tests when updating LiveKit. Never retry a reply
    here: a generation may already have dispatched a tool before failing.
    """

    def __init__(self, adapter, *, turn_detection_disabled: bool = False) -> None:
        self._closing = False
        self._provider_generation = 0
        self._provider_hooks = {}
        super().__init__(adapter, turn_detection_disabled=turn_detection_disabled)

    def _bind(self, child):
        super()._bind(child)
        self._provider_generation += 1
        generation = self._provider_generation
        hooks = []
        for kind in ("metrics_collected", "generation_created"):

            def received(payload, *, event_kind=kind):
                if not self._closing and child is self._active:
                    self.emit(
                        "provider_activity",
                        ProviderActivityEvent(generation, event_kind, payload),
                    )

            child.on(kind, received)
            hooks.append((kind, received))
        self._provider_hooks[child] = hooks
        # Emitted before _update_session can receive an immediate provider event.
        self.emit(
            "provider_child_changed", ProviderActivityEvent(generation, "child_changed")
        )

    def _unbind(self, child):
        for kind, callback in self._provider_hooks.pop(child, ()):
            child.off(kind, callback)
        super()._unbind(child)

    def generate_reply(self, **kwargs) -> asyncio.Future[llm.GenerationCreatedEvent]:
        child = self._active
        try:
            future = super().generate_reply(**kwargs)
        except _PROVIDER_ERRORS as error:
            future = asyncio.get_running_loop().create_future()
            future.set_exception(error)

        def report_failure(done: asyncio.Future[llm.GenerationCreatedEvent]) -> None:
            if done.cancelled() or self._closing or child is not self._active:
                return
            error = done.exception()
            if isinstance(error, _PROVIDER_ERRORS):
                self._on_child_error(
                    llm.RealtimeModelError(
                        timestamp=time.time(),
                        label=child.realtime_model.label,
                        error=error,
                        recoverable=False,
                    )
                )

        future.add_done_callback(report_failure)
        return future

    async def aclose(self) -> None:
        self._closing = True
        await super().aclose()


class GeminiRealtimeFallback(llm.RealtimeModelFallbackAdapter):
    """Select another Gemini session on failure without replaying user actions.

    ``model_changed`` carries ModelChangedEvent after a replacement session is
    selected. It is safe to use for status messages or a tools-disabled startup
    greeting; it does not promise that the provider WebSocket is connected yet.
    """

    def __init__(self, models: list[llm.RealtimeModel]) -> None:
        super().__init__(models, regenerate_on_swap=False)
        self.models = tuple(models)

    def session(
        self, *, turn_detection_disabled: bool = False
    ) -> _GenerationAwareSession:
        session = _GenerationAwareSession(
            self, turn_detection_disabled=turn_detection_disabled
        )
        self._sessions.add(session)
        previous = self.models[0].model

        def changed(event: llm.RealtimeSessionReconnectedEvent) -> None:
            nonlocal previous
            current = session._active.realtime_model.model
            if current == previous:
                return
            change = ModelChangedEvent(previous_model=previous, model=current)
            previous = current
            logger.warning(
                "Gemini Live switched from %s to %s; interrupted actions are not replayed",
                change.previous_model,
                change.model,
            )
            self.emit("model_changed", change)

        session.on("session_reconnected", changed)
        return session


def build_realtime_model(
    *,
    voice: str,
    realtime_input_config: NotGivenOr[types.RealtimeInputConfig] = NOT_GIVEN,
    context_window_compression: NotGivenOr[
        types.ContextWindowCompressionConfig
    ] = NOT_GIVEN,
) -> GeminiRealtimeFallback:
    """Create both native speech-to-speech models using the AI Studio API key.

    Optional overrides are IRA_GEMINI_PRIMARY_MODEL / IRA_GEMINI_FALLBACK_MODEL.
    Only compatible native-audio models are allowed; a text model cannot silently
    replace the voice pipeline. Extended Thinking (gemini-3.8-live-extended-thinking)
    is supported as a high-reasoning fallback or primary model with NON_BLOCKING
    tool behavior and configurable thinking level. Empty environment values use defaults.
    """
    primary_env = os.getenv("IRA_GEMINI_PRIMARY_MODEL", "").strip()
    fallback_env = os.getenv("IRA_GEMINI_FALLBACK_MODEL", "").strip()
    primary = _normalize_model_name(primary_env) if primary_env else GEMINI_LIVE_MODEL
    fallback = (
        _normalize_model_name(fallback_env) if fallback_env else GEMINI_FALLBACK_MODEL
    )
    if primary not in _NATIVE_AUDIO_MODELS or fallback not in _NATIVE_AUDIO_MODELS:
        raise ValueError(
            "Gemini primary and fallback must be supported native-audio models"
        )
    if primary == fallback:
        raise ValueError("Gemini fallback must differ from the primary model")

    thinking_level = os.getenv("IRA_THINKING_LEVEL", "low").strip() or "low"

    def _create_child_model(model_name: str) -> google.realtime.RealtimeModel:
        thinking_cfg: NotGivenOr[types.ThinkingConfig] = NOT_GIVEN
        behavior: NotGivenOr[types.Behavior] = NOT_GIVEN
        if model_name == GEMINI_EXTENDED_THINKING_MODEL:
            thinking_cfg = types.ThinkingConfig(thinking_level=thinking_level)
            behavior = types.Behavior.NON_BLOCKING

        return google.realtime.RealtimeModel(
            model=model_name,
            vertexai=False,
            modalities=[types.Modality.AUDIO],
            voice=voice,
            temperature=0.6,
            image_encode_options=images.EncodeOptions(format="JPEG", quality=85),
            realtime_input_config=realtime_input_config,
            context_window_compression=context_window_compression,
            thinking_config=thinking_cfg,
            tool_behavior=behavior,
        )

    return GeminiRealtimeFallback(
        [_create_child_model(primary), _create_child_model(fallback)]
    )
