# Fixes and monitoring

## Immediate action

No emergency fix is required for one event-loop warning lasting exactly 100 ms. Continue watching the session. The Gemini plugin already began reconnecting, which is the expected recovery path for a realtime session nearing its server lifetime.

If the agent becomes silent or fails to answer after reconnecting:

1. Stop the console process with `Ctrl+C`.
2. Start it again from the project directory:

```powershell
uv run python src/agent.py console
```

3. Confirm the correct microphone is selected and test with a short spoken command.

## Code-side protections

The important rule is: do not execute blocking work directly inside an async LiveKit callback, tool, or event handler.

Keep using a worker thread for:

```python
result = await asyncio.to_thread(blocking_function, argument)
```

In this project, that applies to:

- Selenium and browser navigation.
- PyAutoGUI and Windows desktop control.
- SQLite memory and workflow operations.
- File-system work that can take an unpredictable amount of time.

Use an async client for network calls where one is available. Do not call `requests`, synchronous database APIs, `time.sleep()`, Selenium, or PyAutoGUI directly on the agent event loop.

## Investigation if the warning repeats

### 1. Look for a pattern

Record whether warnings happen:

- Only during Gemini reconnects.
- When a voice tool is called.
- When Spotify or Brave is starting.
- During a large page read or workflow render.
- At the same time as audio dropouts.

One warning near 100 ms is low severity. Repeated warnings over 100 ms, especially above 500 ms, deserve investigation.

### 2. Check the complete log around the warning

Capture at least 10 seconds before and after each warning. The event-loop warning's `stack` may contain a useful call site when the block lasts long enough. The current log says `# no sample`, so it does not identify the blocking function.

### 3. Check for synchronous operations added later

Search new code for these common causes:

```text
requests.
time.sleep(
subprocess.run(
timeout=
open(
Path.read_text(
Path.write_text(
```

Move unpredictable or blocking work into `asyncio.to_thread`, or replace it with an async API where practical.

### 4. Check the machine during recurrence

On Windows, check whether CPU, memory, disk, or antivirus activity spikes at the same time. `cpu_time: 0.0` in this event does not prove that the entire computer was idle; it only means the watchdog did not attribute CPU time to the sampled block.

### 5. Check Gemini and LiveKit dependency versions

Run these from the project directory and record the output when troubleshooting:

```powershell
uv run python --version
uv run python -c "import livekit, selenium; print('livekit and selenium import successfully')"
uv lock --check
```

Update dependencies only after checking the project's compatibility and release notes. A dependency update is not required by this single warning.

## Microphone checks

The microphone label in the log is not itself a failure. If audio input is actually missing:

1. Check Windows **Settings > System > Sound > Input**.
2. Select the intended `Microphone Array (Intel(R) Smart ...)` device.
3. Confirm its input level changes while speaking.
4. Check that microphone access is enabled for the terminal or application.
5. Restart the console agent after changing the default input device.

## Browser and Spotify checks

The event-loop warning is separate from the earlier Brave profile-lock problem. For Spotify automation:

- Close normal Brave windows before Selenium uses the signed-in Brave profile.
- Set `IRA_BROWSER=brave` before starting the agent.
- Keep browser operations behind the existing thread wrapper.
- Treat a browser startup error as a browser/profile problem, not as a Gemini audio problem.

## Recommended conclusion for this incident

Classify the captured event as **transient, low-severity latency during Gemini realtime session replacement**. No source-code change is justified from this log alone. The existing code already follows the main blocking-work mitigation, and the next useful evidence would be a longer event-loop block with a captured stack or repeated warnings correlated with a specific tool.

## Sources consulted

- [LiveKit Agents documentation](https://docs.livekit.io/agents/)
- [Gemini Live API overview](https://ai.google.dev/gemini-api/docs/live-api)
- [LiveKit Gemini realtime plugin documentation](https://docs.livekit.io/agents/models/realtime/plugins/gemini/)
