"""Greeting recovery never replays tools or hides failed speech handles."""

import asyncio
from types import SimpleNamespace

import agent


class FakeSpeech:
    def __init__(self, error=None):
        self.error = error

    def exception(self):
        return self.error

    def __await__(self):
        async def wait():
            return self

        return wait().__await__()


async def test_retries_failed_startup_speech_only_after_model_swap():
    swapped = asyncio.Event()
    calls = []

    def reply(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            asyncio.get_running_loop().call_soon(swapped.set)
            return FakeSpeech(RuntimeError("primary unavailable"))
        return FakeSpeech()

    assert await agent._greet_on_start(SimpleNamespace(generate_reply=reply), swapped)
    assert len(calls) == 2
    assert all(call["tool_choice"] == "none" for call in calls)


async def test_successful_greeting_is_not_repeated():
    calls = []

    def reply(**kwargs):
        calls.append(kwargs)
        return FakeSpeech()

    assert await agent._greet_on_start(
        SimpleNamespace(generate_reply=reply), asyncio.Event()
    )
    assert len(calls) == 1


async def test_exhausted_greeting_stops_after_fallback():
    swapped = asyncio.Event()
    swapped.set()
    calls = []

    def reply(**kwargs):
        calls.append(kwargs)
        return FakeSpeech(RuntimeError("all providers unavailable"))

    assert not await agent._greet_on_start(
        SimpleNamespace(generate_reply=reply), swapped
    )
    assert len(calls) == 2
