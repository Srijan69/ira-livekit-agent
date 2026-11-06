# Scoped whole-desktop implementation evidence

Date: 18 October 2026. Checkout: `IRA-LiveKit_Agent`. Changes remain local beside
the existing uncommitted implementation. Design approval is recorded in
`docs/superpowers/specs/2026-10-18-scoped-whole-desktop-design.md`.

## Changes

- Physical monitor discovery and capture share a per-thread physical-DPI context.
  Full captures include all screens, their origin and immutable topology. Input
  accepts signed native points only inside actual monitor rectangles.
- Overview and detail images carry an explicit physical source rectangle. The
  only image-to-native conversion is
  `origin + floor((pixel + .5) * source_extent / image_extent)`.
  Detailed views retain the original capture and observation age. Topology drift,
  monitor gaps and stale window/control identities cause zero input.
- Named Windows accessibility targets remain preferred. Observed window IDs
  permit explicit focus followed by verified recapture. Move-only actions may
  traverse monitors; effects retain foreground and ownership checks.
- Actual human input binds exported tools and every saved-skill step to a request.
  Gemini admission compares the request, arguments, recent outcomes, exact screen
  image, target metadata and bounded marked crops at proposed points. Screenshot
  messages cannot grant authority. Stop, completion, expiry, model/session changes
  and new instructions invalidate old permits.
- A settled final report is also assessed for completion when the model omits
  `finish_request`. The check uses actual outcomes and the current image. Running
  or admission-pending tools, unfinished speech and pending questions prevent
  closure. Request identity and activity revision are checked again after the
  policy await. Explicitly stopped unresolved tasks are not labeled successful.
- A pending clarification requires a real human answer. Invalid skill answers
  leave the question available for correction. The actual skill engine regression
  rejects boolean `true` for integer X, then accepts `30` and emits exactly `[30]`.
- Native input rechecks the permit after host-lock acquisition, after pointer
  marker startup and immediately before effects. Interrupted drags release their
  held mouse button. Start-menu launch rechecks between Win, typing and Enter.
- Owned captures and model-image history are bounded. Windows thread desktop
  handles are restored and closed before saving a PNG, including error paths.
  Session teardown detaches scope hooks and closes the owned policy client.

## Coordinate and cancellation evidence

Synthetic topology: primary `(0,0,1920,1080)` plus left monitor
`(-1280,-200,1280,1024)`, giving virtual bounds `(-1280,-200,3200,1280)`.
The delivered overview is `1024×410`; image `(204,128)` clicks native
`(-641,201)`. Native pointer `(-640,200)` is reported in image pixel `(204,128)`.
Cursor projection uses the containing pixel, so the far desktop corner stays
inside the delivered image. A cursor outside a detail crop is reported as null.
A primary-screen overview crop `(400,100,400,300)` represents physical source
`(750,187,750,563)`; detail pixel `(100,100)` clicks `(850,287)`.

Native tests initially exposed missing negative-origin support, DPI mismatches,
desktop-handle leaks and orphaned capture files. Independent review identified
the Start-menu cancellation gap. Four new cases reproduced continued typing or
Enter after Stop/new input during either wait; all pass after guarding each step.
The actual exported Assistant integration subset passes eight cases.

## Live verification status

Policy-only synthetic tests passed nine cases on `gemini-3.5-flash-lite`, including
read-only requests, unrelated app launch, ambiguous profiles, and both correct
and wrong pixel targets without UI Automation metadata. The wrong visual target
was incorrectly allowed before adding bounded marked target crops. Policy
outages raise errors and do not count as successful semantic denials.

Owned native profile and indicator suite: **4 passed**. The native target bounds
were `[579,298,270,120]`; delivered image size `1024×576`, physical desktop
`1920×1080`, origin `(0,0)`. Both target-ID input and image pixel `(380,190)`
selected `kommidi`, each independently recorded by the fixture's `WM_COMMAND`.
The first result file was removed before the second click. Three overlay checks
verified visibility, click-through behavior, unchanged focus and destruction on
failure. The fixture now supports standard dialog keyboard navigation, allowing
the desktop helper to focus Start with Tab and activate it with Space.

Earlier native attempts correctly refused a click when foreground changed to
Codex, or timed out awaiting the user. Those attempts are not counted as passed.
The actual Assistant/provider checks passed two cases through the production
typed-input callback. The profile test performed capture, exact named-target
click at native `(1420,385)`, fresh image, final report and request closure.
Separate move and scroll requests also completed and closed. The early harness
used `wait_for_idle()`, which can return while background tools still run;
teardown then correctly triggered cancellation guards. The harness now awaits
actual request closure. Gemini sometimes omitted `finish_request` despite
reporting the verified result, which exposed and motivated the completion
fallback rather than relaxing the terminal check.

The completion-specific prompt also has live negative coverage: an unchanged
picker does not substantiate "profile opened", future intent remains open, and
a pending question does not close. A real observed final state can close; an
explicit unresolved stop can close without being called successful. A real SDK
SpeechHandle regression verifies that an initial voice generation registers its
completion callback before the finalized transcript supplies request identity.

## Final automated checks

- `uv run --no-sync python -m pytest -q --tb=short`: **797 passed, 28 skipped**.
  Opt-in provider/native tests are run separately. The warning comes from an
  upstream Google SDK deprecated Python typing alias.
- Scoped Ruff lint and format checks: **21 files clean**.
- Combined live Assistant/policy run: **15 passed**, with the last case failing
  only because Gemini returned HTTP 429 and a 56-second retry delay. After
  respecting that backoff, the isolated pending-question case **passed**.
  All **16 live cases** passed across those runs; the rate-limit failure was not
  counted as a successful semantic denial. No production code changed between
  the combined run and retry.
- Owned native profile/indicator run: **4 passed**.
- Independent review: no unresolved findings; 13 focused completion and
  clarification cases passed, with pending-work and stale-verdict races covered.

Commands for the live checks (use the isolated retry after the provider's delay
if the free tier rejects a burst):

```powershell
$env:IRA_RUN_LIVE_EVALS = "1"
$env:IRA_RUN_SCOPE_LIVE = "1"
uv run --no-sync python -m pytest tests/test_computer_use_live.py tests/test_tool_access_live.py tests/test_request_scope_live.py -q --tb=short
uv run --no-sync python -m pytest tests/test_request_scope_live.py -k choose -q --tb=short
```

`git -c core.safecrlf=false diff --check` passes. No changes were committed,
published or deployed; unrelated workspace changes were preserved.

## Limits

The attached hardware has one monitor, `DISPLAY1`, at `(0,0,1920,1080)`.
Negative-origin and multiple-monitor behavior is covered by deterministic
synthetic tests; a physical second-monitor check has not been performed.
Model interpretation remains probabilistic and provider availability can delay
or reject otherwise valid actions. The current free tier exposed a 15-requests-
per-minute limit for the policy model during combined live verification; actions
require a fresh successful policy check and are refused when it is unavailable.
This does not claim universal Codex parity.
The primary voice architecture, Selenium browser and blue IRA pointer marker
are retained; the Open Computer Use runtime is not embedded.

Restart the local IRA agent and reconnect to load the new tools and input hooks.
