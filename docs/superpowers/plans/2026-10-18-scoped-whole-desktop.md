# Scoped Whole-Desktop Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or
> superpowers:executing-plans task by task. Independent topology and request
> admission modules may use superpowers:dispatching-parallel-agents; integration
> and dependent edits remain sequential.

**Goal:** Capture and control all monitors accurately while limiting IRA's
actions to the user's current requested task.

**Architecture:** Retain the current native controller and observed-frame loop.
Add explicit physical topology/view transforms and a shared request-admission
controller in front of exported voice/browser tools and saved-skill steps.

**Tech Stack:** Python, Pillow, Windows APIs, LiveKit/Gemini, Selenium, pytest.

**Spec:** `../specs/2026-10-18-scoped-whole-desktop-design.md`

## Global constraints

- Use the renamed checkout and `uv run --no-sync python -m ...`.
- Retain `src/agent.py` and existing Gemini Live/Selenium/skill architecture.
- Preserve unrelated uncommitted work. No automatic commits or publication.
- Test observable effects, including zero effects on rejected requests.
- Model interpretation is not a deterministic security proof; request identity,
  cancellation, target freshness and coordinate math must be deterministic.

## Task 1: Physical desktop topology and capture

Files: create `src/desktop_layout.py`, `tests/test_desktop_layout.py`; modify
`src/desktop.py` and relevant desktop tests.

Produces `Monitor(id: str, bounds: tuple[int,int,int,int], primary: bool)`,
`DesktopLayout(monitors: tuple[Monitor,...])`, `.bounds`, `.contains(x,y)`;
`DesktopController.layout()`; `ScreenCapture.origin`, `.layout`.

- [x] Write failing fixtures with a 1920x1080 primary and a 1280x1024 monitor at
  (-1280,-200). Assert virtual bounds (-1280,-200,3200,1280), full capture pixels,
  accepted (-640,200), rejected (-1000,1000), and topology-drift rejection.
- [x] Run `uv run --no-sync python -m pytest tests/test_desktop_layout.py -q`.
- [x] Implement physical monitor discovery and all-screen capture. Keep the
  injected backend boundary for tests; validate real geometry before input.
- [x] Verify signed movement/click/scroll/drag and guaranteed button release;
  run the focused desktop regression files.

## Task 2: Request admission and lifecycle

Files: create `src/request_scope.py`, `src/request_scope_livekit.py`,
`tests/test_request_scope.py`, `tests/test_request_scope_livekit.py`.

Produces tool wrappers, lifecycle attachment, a context-local permit check and
saved-registry wrapping. Parent integrates through the exported documented API.

- [x] Write failing tests with literal user requests and an injected semantic
  judge: observe-only rejects click, wrong app rejects launch, a completed task
  rejects another click, new request/Stop invalidates a queued permit, duplicate
  transcript does not reopen a request, and screenshot messages do not authorize.
- [x] Run the new test files and confirm missing-feature failures.
- [x] Implement immutable request identity, bounded history/admission, one-use
  permits, cancellation and Gemini structured decisions. Validate malformed and
  timed-out decisions before any side effect; permit a legitimate multi-step
  task and reject unrelated effects.
- [x] Wrap the exported FunctionTool boundary while preserving schema/flags.
  Attach finalized voice events and typed input; correlate continuation calls.
  Saved-skill wrappers must retain the original scope through waiting/resumption.
- [x] Verify the actual Assistant tool surface plus synthetic live Gemini cases.

## Task 3: Observed views, focus and integration

Files: modify `src/computer_use.py`, `src/agent.py`, `src/skill_tools.py`,
`src/skill_adapters.py`; create `tests/test_desktop_views.py` and integration tests.

- [x] Write failing hand-derived cases. A view of (-1280,-200,3200,1280) at 1024x410
  maps image (204,128) to native (-641,201). A detail view must preserve full
  overview pixels and reject stale IDs/topology before input.
- [x] Implement full-frame/view rectangles, detail recapture and metadata.
  Use `origin + floor((pixel + .5) * source_extent / image_extent)` once.
- [x] Add observation-scoped window listing/focus with exact identity and fresh
  post-focus capture. Keep mutation actions inside their verified foreground;
  permit move-only requests anywhere on real observed monitor rectangles.
- [x] Install request wrappers in Assistant and check current permits after lock
  acquisition and immediately before native effects. Add Stop/finish tooling.
- [x] Bound capture file and model-image retention. Verify provider receives
  exact view dimensions and target bounds use the same transform.

## Task 4: Verification and delivery

- [x] Run focused tests, fix failures at their source and review the combined
  scope/capture/input boundaries independently.
- [x] Run `uv run --no-sync python -m pytest -q --tb=short` and scoped Ruff.
- [x] Run owned native fixtures and synthetic live model checks; record all
  connected-monitor coverage and unresolved limitations honestly.
- [x] Update README/research evidence; run
  `git -c core.safecrlf=false diff --check`.

## Progress

- Approved design recorded. No repeat approval is needed for this implementation.
- Topology and admission have separate file ownership; parent owns integration.
- Existing checkout retained because the prerequisite implementation is dirty.
- Native click/indicator suite: 4 passed on the attached 1920x1080 display.
  Synthetic negative-origin topology/view tests and cancellation checks pass.
- Live policy checks: 9 passed. Full Assistant evaluation exposed a model that
  performed the correct click and reported success but omitted `finish_request`.
  A settled-report completion fallback now checks the actual image/outcomes,
  pending work, request identity and activity revision before closing the request.
- Final backend suite: 797 passed, 28 opt-in tests skipped. Scoped Ruff: 21 files
  clean. Independent review has no unresolved findings. See the final evidence
  document for live/provider results and rate-limit retries.
- All 16 live cases passed across the combined run and one isolated retry after
  the provider's requested quota backoff. Native suite: 4 passed. Implementation
  and verification are complete; restart/reconnect IRA to load the updated tools.
