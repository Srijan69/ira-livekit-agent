# Whole-desktop capture and request scoping: feasibility findings

Date: 18 October 2026. This records the pre-implementation investigation. The
user subsequently approved the design and implementation. See the
[implementation evidence](2026-10-18-scoped-whole-desktop-verification.md) for the
current behavior and test results; descriptions of old code below are historical.

## Confirmed requirements

The user chose both of these behaviors during design clarification:

- IRA may complete the necessary steps for the requested task, then stops. It
  asks when the target or intent is unclear.
- Screen capture and cursor control cover every connected monitor.

The earlier primary-monitor Chrome targeting fix is implemented separately.
Its verification does not establish these expanded requirements.

## Display coordinates

Current `DesktopController.screen_size()` reads PyAutoGUI's primary-display
size. `_validate_point()` accepts only nonnegative points inside that rectangle.
`ObservedDesktop` records width and height but no virtual-desktop origin or
monitor topology. Its image-to-native transform assumes origin `(0, 0)`.

Installed Pillow exposes `ImageGrab.grab(all_screens=True)` on Windows. Its
implementation obtains an offset, size and pixels from the native screen grab;
the returned PIL image does not expose the desktop offset. Therefore enabling
all-screen capture alone would still leave input coordinates incorrect.

The implementation must explicitly record the virtual desktop origin, physical
monitor rectangles and the source rectangle of each delivered image. Capture
and topology must be checked under the same physical-DPI context. Reject points
in gaps between monitors even when they fall inside the overall bounding box.
Changing topology invalidates existing observations.

Installed PyAutoGUI uses `SetCursorPos` for movement but primary-display
dimensions inside its legacy mouse-event conversion. Inspecting this source
does not prove secondary-monitor clicks fail: button flags may ignore the
converted coordinates. It does mean that relaxing IRA's bounds validation is
not sufficient evidence of correct multi-monitor input. Native tests must
verify movement, button events, scroll and drag independently.

Detailed views should be derived from the same complete captured frame and
carry their own source rectangle. An overview alone can shrink small controls
too much. The existing no-resize provider encoding must remain consistent with
the exact dimensions delivered in tool metadata.

## Binding actions to spoken and typed requests

IRA currently lets Gemini perform turn detection. Current LiveKit documentation
states that `on_user_turn_completed` requires agent-side turn detection when
using a realtime model. The installed `AgentActivity` also returns before that
hook when realtime turn detection is enabled. That hook alone cannot establish
IRA's action scope.

The installed Google provider first emits partial input transcripts. Final
transcription is emitted by `_mark_current_generation_done()`. Importantly,
`_handle_tool_calls()` queues function calls and then calls that finalization
method synchronously, without an intervening await. This offers an integration
path through `user_input_transcribed` before queued tool coroutines execute;
its ordering and request correlation still need regression and live tests.

Use finalized human-input events, explicit request identity and tool-generation
binding, rather than selecting the last user-role chat message. IRA currently
delivers screenshot evidence in a user-role message; it must never become a
new request or broaden permission. Missing or mismatched human input must not
fall back to an older request. Tool-result continuations must retain the current
task identity, while a new instruction or cancellation invalidates old calls.

For typed dashboard input, LiveKit exposes `TextInputOptions.text_input_cb`.
The handler can establish request identity before generating the response.
Both typed and voice requests must pass through the same task controller.

`_run_host_action()` currently shields the worker so cancellation cannot release
host ownership while physical input is still running. Preserve that cleanup
guarantee, add scope checks after acquiring ownership and immediately before
effects, and reject queued calls from a stopped task. Saved skills, direct
desktop tools and browser tools must not bypass the shared controller.

## Required acceptance evidence

- Full native capture plus delivered overview/detail image dimensions and
  transforms agree across mixed scaling and negative desktop origins.
- Inputs reach independently observed targets on each connected monitor;
  monitor gaps and changed topology cause zero input.
- Screenshot-only and conversational requests cause no desktop/browser effects.
- Exact requested controls are selected; absent or duplicate matches lead to
  clarification, with no guessed click.
- Voice transcript/tool ordering, typed input, tool continuations, screenshot
  evidence, interruptions and model swaps cannot reuse another request's scope.
- Stop prevents queued and later actions; an in-flight drag releases its button
  and never becomes a false successful task after cancellation.

## Sources

- Current checkout: `src/desktop.py`, `src/computer_use.py`, `src/skill_tools.py`,
  `src/agent.py`, `src/realtime_model.py`.
- Installed source: Pillow `ImageGrab.py`, PyAutoGUI `_pyautogui_win.py`, LiveKit
  `voice/agent_activity.py`, `voice/events.py`, and Google
  `realtime/realtime_api.py`.
- [LiveKit pipeline nodes and hooks](https://docs.livekit.io/agents/logic/nodes.md)
- [LiveKit text and transcription](https://docs.livekit.io/agents/multimodality/text.md)
- [LiveKit events](https://docs.livekit.io/reference/agents/events.md)
- [Earlier targeting evidence](2026-10-18-computer-use-targeting-fix.md)
