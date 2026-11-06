# Scoped whole-desktop computer use

Approved in chat on 18 October 2026: “yeah implement it”. This records the
design approved after the user chose task completion (necessary steps, then
stop) and coverage of every connected monitor.

## Behavior

1. Capture the complete virtual desktop, including monitors left/above the
   primary display. Deliver a full overview plus on-demand detailed views of
   the same capture. Preserve physical monitor rectangles and exact view-to-
   desktop transforms; reject gaps and stale topology.
2. Prefer fresh named accessibility targets. Coordinate actions use the exact
   delivered image pixels. Move-only operations may traverse the desktop;
   clicks, typing and drags retain foreground/ownership validation. An explicit
   named-window focus operation permits switching applications and monitors,
   followed by a fresh observation before further input.
3. Bind model-visible computer/browser/skill actions to the current real human
   request. Screenshot evidence never creates authority. Necessary steps are
   permitted; unrelated effects, ambiguous targets and actions after completion
   are refused. Screen-inspection requests permit observation only.
4. Use one action-admission boundary for direct tools and each saved-skill step.
   A semantic check compares proposed effects with immutable user instructions,
   recent action results and observed target metadata. It cannot create a new
   user request. Missing/invalid policy results fail closed. Natural-language
   interpretation remains model-dependent; deterministic checks enforce request
   identity, one-use permits, cancellation, freshness and action limits.
   If the model omits `finish_request`, assess its settled final report against
   actual results and the latest image before closing. Pending tools, questions,
   intermediate narration and a changed request prevent that closure. An
   explicitly stopped, unresolved task may close without being called successful.
5. Stop revokes pending permits and stops the active skill. New instructions
   cannot inherit queued effects from an older request. Keep ownership of an
   in-flight native action through cleanup; always release held mouse buttons.
6. Preserve Gemini Live voice, Selenium browser support, the existing blue IRA
   pointer marker and the strict saved-skill engine. Do not embed the upstream
   Open Computer Use runtime or claim universal Codex parity.

## Components and interfaces

- `desktop_layout.py`: frozen `Monitor(id, bounds, primary)` and
  `DesktopLayout(monitors)`, with virtual `bounds` and `contains(x, y)`.
- `DesktopController.layout()` discovers physical topology. `capture_screen()`
  captures every monitor and returns additive `ScreenCapture.origin` and
  `ScreenCapture.layout` metadata. Existing raw native coordinates retain their
  meaning and may be signed where a real monitor exists.
- `ObservedDesktop`: holds the full native frame, overview/detail source
  rectangle, topology and observation-scoped target/window identities. Each
  action consumes its observation; detail capture changes view without losing
  original full-resolution data. Provider encoding does not resize again.
- `request_scope.py`: current request, bounded in-memory history, cancellation,
  semantic admission and one-use action permits. A Gemini text-model check uses
  existing credentials; no unrelated provider/runtime is introduced.
- `request_scope_livekit.py`: wraps model-visible tools without changing their
  schemas, binds finalized voice/typed human input, propagates permits using
  context variables, and wraps saved-skill registry handlers. Internal desktop
  primitives remain usable independently; externally exposed tools are gated.
- Agent integration is thin: install wrappers before connection, attach input
  lifecycle hooks, check permits again under the host lock and at physical input
  boundaries, expose detail/focus/stop/finish tools and image metadata.

## State and errors

No current request means no model-visible host effects. Requests have explicit
IDs; duplicate transcript deliveries do not restart them. Tool continuations
retain the original request. Changed requests/session swaps, Stop, expiry and
completion invalidate permits. Policy failures request clarification without
input. Unknown input outcomes are recorded as uncertain and are never replayed
automatically. Captures crossing window transitions retry observations only.

Screenshot files and image history are bounded and cleaned by their owner.
Capture failure must not leave an old actionable frame. Monitor enumeration
failure must not silently substitute primary-only capture in Windows production.

## Acceptance evidence

Tests first demonstrate the missing behaviors: negative origins, mixed monitor
sizes/scaling, monitor gaps/topology changes, detail-coordinate transforms,
named targets and background-window focus, unchanged encoder dimensions,
read-only requests, unrelated effects, duplicate/stale requests, cancelled
waiters, interrupted drags, tool-result continuations and saved-skill bypass.

Run focused tests, the full backend suite, scoped lint and whitespace checks.
Run live Gemini policy/tool tests with synthetic desktop evidence. Use owned
native fixtures and independent received-event records for physical input;
report actual attached-monitor coverage separately from synthetic layouts.
Recheck the Kommidi-selection behavior without exposing personal screen content
to a provider unnecessarily. No success claim rests on input dispatch alone.

## Workspace and delivery

Use `C:\Users\kommi\VS code projects\IRA-LiveKit_Agent` and `uv`. Keep
`src/agent.py` as the entrypoint. Preserve all unrelated dirty work; do not create
a fresh worktree that omits the earlier uncommitted implementation. Do not commit
or publish unrelated changes. User approval authorizes implementation without
another design/execution-choice question.

## Implementation status

Implemented and validated in the renamed checkout. Physical topology, detailed
views, request admission, clarification, cancellation and completion share the
existing native controller and LiveKit runtime. See the
[verification record](../../research/2026-10-18-scoped-whole-desktop-verification.md)
for the test commands, results and one-physical-monitor coverage limit.
