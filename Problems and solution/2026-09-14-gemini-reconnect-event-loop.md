# Incident: Gemini reconnect and event-loop warning

**Date:** 2026-09-14  
**Project:** `livekit-voice-agent`  
**Room:** `console`  
**Input device shown by the console:** `Microphone Array (Intel(R) Smart ...` (the name is truncated in the captured output)

## Console log

```text
16:28:19.445 DEBUG    livekit.agents     conversation_item_added
                                     {"role": "user", "lk.pii.text": ".", "room": "console"}
16:28:19.461 DEBUG    livekit.agents     conversation_item_added
                                     {"role": "assistant", "lk.pii.text": "The song is currently paused, boss. Did you want to close Spotify or something else?", "room": "console"}
16:28:48.417 WARNING  livekit.plugins.google Gemini server indicates disconnection soon. Time left: 50s
                                     {"room": "console"}
16:28:48.421 DEBUG    livekit.plugins.google send task finished.
                                     {"room": "console"}
16:28:48.504 DEBUG    livekit.plugins.google connecting to Gemini Realtime API...
                                     {"room": "console"}
16:28:51.318 WARNING  livekit.agents     event loop blocked for 100ms; synchronous work on the
                                     agent loop delays audio and turn handling, move it to
                                     a thread or an async client
                                     {"duration": 0.1, "threshold": 0.1, "gc_time": 0.0,
                                      "cpu_time": 0.0,
                                      "stack": "# no sample: the block ended before the watchdog looked",
                                      "room": "console"}
```

## What the messages mean

### 1. Gemini is rotating the realtime session

`Gemini server indicates disconnection soon. Time left: 50s` means the Gemini Live realtime WebSocket session is approaching its server-managed lifetime. The LiveKit Google plugin closes the old send task and opens a new realtime connection.

This is a session lifecycle event, not the same as a microphone failure. The following `connecting to Gemini Realtime API...` line shows that reconnection started automatically.

The Gemini Live API is a stateful WebSocket service intended for low-latency streaming audio. Long-running voice agents must therefore tolerate session replacement and reconnects.

### 2. The event loop was blocked for 100 ms

LiveKit's asyncio watchdog measured the Python agent event loop unable to progress for approximately `0.1` seconds. The configured warning threshold is also `0.1` seconds, so this is a boundary-level warning.

While the loop is blocked, audio and turn-handling tasks can be delayed briefly. One isolated 100 ms warning usually causes, at most, a small moment of latency. Repeated warnings or blocks lasting seconds can produce missed audio, delayed replies, or awkward interruptions.

The log says:

- `gc_time: 0.0`: garbage collection was not identified as the cause.
- `cpu_time: 0.0`: the watchdog did not identify a measurable CPU-heavy section.
- `stack: # no sample`: the block ended before the watchdog sampled the executing stack.

This means the exact blocking line cannot be identified from this event alone.

### 3. The microphone name is not an error

`Microphone Array (Intel(R) Smart ...)` is the Windows audio input device label. It appears because the console is showing the selected microphone. Nothing in this excerpt indicates that the microphone stopped working.

## Local code check

The project already moves the known blocking operations off the event loop:

- Selenium browser calls use `asyncio.to_thread(...)` in `src/browser.py` through `Assistant._browser_call`.
- PyAutoGUI desktop operations use `asyncio.to_thread(...)` in `src/agent.py`.
- SQLite memory and workflow operations also use `asyncio.to_thread(...)`.

Therefore, this single warning is not enough evidence to identify a defect in the browser or Spotify workflow. It happened close to Gemini reconnect, but the timing alone does not prove that reconnect caused the stall.

## Impact for this incident

The assistant continued to reconnect to Gemini. The log does not show a crash, failed microphone initialization, or failed Spotify action. Treat this event as a transient latency warning unless it occurs repeatedly or is accompanied by audible dropouts or failed turns.

## References

- [LiveKit Agents documentation](https://docs.livekit.io/agents/)
- [Gemini Live API overview](https://ai.google.dev/gemini-api/docs/live-api)
- [LiveKit Gemini realtime plugin documentation](https://docs.livekit.io/agents/models/realtime/plugins/gemini/)
