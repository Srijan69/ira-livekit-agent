# Playwright browsing and observed computer use

User scope: remove Selenium completely from IRA's runtime and dependencies,
upgrade browser execution to Playwright, improve desktop fallback and tool
timing, and provide both an IRA screen/action preview and a subtle desktop
indicator. The user selected both visual views on November 7, 2026.

## Browser

Replace the browser implementation with native Playwright Python APIs. A single
dedicated owner thread owns Playwright, the persistent BrowserContext, Pages and
Locators. Synchronous public BrowserController methods marshal onto that thread
with copied request contextvars; existing host ownership and cancellation guards
remain effective. Do not emulate the removed driver's API. Keep Chrome/Brave
choices, IRA's existing persistent profiles, Spotify's Brave-only preference,
numbered controls, tabs, forms, selectors, observations and playback verification.

Use native locator actions and bounded auto-waits. A captured numbered reference
must not retarget a replacement DOM element. Do not force JavaScript clicks past
an overlay. Navigation/launch failure cannot substitute browsers or replay an
uncertain action. Expose marshalled current_url, is_open, evaluate(expression,
arg=None), and page_html() for fixed internal extractors and evaluation scripts.

## Computer use

Keep observe → one guarded action → fresh observation. Revalidate control
identity around screenshots and keyboard focus before typing. Add Unicode input
using native Windows SendInput without clipboard mutation; retain printable text
bounds and per-character authority/focus checks. Screen evidence and target IDs
remain tied to the exact request, model session and observation.

ObservedDesktop.act and focus accept an optional _on_action(frame, descriptor)
callback after target resolution and final preflight but before input. The
descriptor contains only action, observation_id, image_points, and optional
observed target/window metadata. Never include typed text. Recheck authority
after telemetry. Failed input/refresh is uncertain and cannot reuse old evidence.

The click-through, nonactivating native indicator receives actual target points,
draws a target ring, click cue or drag endpoints/path, and cleans up on cancellation,
parent crash and normal completion. It cannot change the system cursor or steal
focus.

## Visual event contract

Use LiveKit text streams on `computer.observation`, not oversized data packets.
An event is a JSON object with `type='computer.observation'`, `run_id`, `call_id`,
`tool_name`, increasing `sequence`, and `phase` in observed/before/after/unavailable/
cleared. A frame carries `observation_id`, `captured_at` (ISO timestamp),
`image_size=[width,height]`, `screenshot_data_url` (JPEG/PNG only), bounded observed
`targets` (id/name/role/image_bounds), `windows`, and cursor_image_position.
Optional action carries `action`, `observation_id`, `image_points`, and observed
target/window metadata. All coordinates refer to the accompanying image.

Limit each encoded event to 512 KiB. Stream sends stay ordered, asynchronous,
bounded, and never block the voice loop. Drop screenshots while disconnected;
do not replay them on reconnect. The receiver validates the current room and
AGENT participant before and after bounded stream reading, rejects malformed
images/geometry/sequences, and clears evidence on stop/reconnect/session end.

The dashboard preserves its existing theme and curved execution panel. An
expandable Computer Use preview shows real before/after frames, target outlines,
action markers, bounded readable action history, timestamps and freshness.
A keyboard-accessible enlarged view makes small targets legible. Disconnected,
stale, failed, and unobserved states must be explicit. It is an inspection view;
screenshots are never invented and are not saved to profile or memory.

## Tool routing and evaluation

Playwright handles browser pages first; named desktop controls/native dialogs use
observed computer use. On a browser locator/overlay failure, preserve scope and
return a specific next-step instruction to capture the screen; do not silently
repeat an uncertain effect. Inspect the fresh image before desktop input and
inspect its result before success. Successful desktop input invalidates old
browser numbering. Explicit Spotify requests continue to use the dedicated
verified tools and saved Brave preference.

Evidence required: complete backend/dashboard checks, removal search for active
driver imports/dependencies, real isolated Playwright browser tests, guarded
desktop/focus/Unicode/overlay tests, real renderer preview inspection, and
provider routing evaluation with an isolated host. Recheck the authorized Aaya
Sher request in Brave through the new browser where the logged-in profile is
available. Report measured limits without certifying every task.

Preserve all prior uncommitted profile/context/Hermes changes and existing data.
No commits, remote deployment or existing-session restart are required for this
goal. Browser and desktop runtime objects close under their owning worker after
in-flight native work settles.
