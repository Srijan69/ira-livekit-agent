# Tool access, complete mouse control, and execution display

The active goal is to make IRA reliably use her available tools, retain the adopted Hermes skill features, cover mouse click/scroll/move/drag operations, and show actual execution next to the orb as in the supplied image.

This continues the user's approved implementation. The previous milestone made progress but does not prove this full goal: the ExecutionStatus component is currently unmounted, scroll/drag lack voice wrappers, and Gemini's installed provider declares immutable tools and restarts its connection when update_tools changes the schema.

## Design and verification

- Register all existing capability schemas before Gemini's session starts. Keep skill bodies/catalogs progressively loaded. Calling load_capabilities remains compatible and must not restart the provider when schemas already exist. Prove through a real provider configuration boundary and no-update tests, not only a list of Python methods.
- Expose bounded vertical/horizontal scroll, multi-click, cursor movement and drag. Validate the entire operation before input, execute through the shared host lock, and release a pressed drag button in finally. Add guarded compiled adapters for these mouse operations and retain typed outcomes/checks. Test dispatch, invalid arguments, cleanup and cancellation ownership using an injected backend.
- Mount the execution panel beside the orb on Home; use a readable vertical timeline, elapsed time, actual registered tool names, and numbered skill-step records. Show waiting, verifying, failed, cancelled, uncertain, unavailable and done honestly. No invented activity or raw argument/exception content. Preserve responsive layouts and existing dashboard styling.
- Extend skill telemetry additively with step_tool and step_outcome, maintaining outer tool_name/call_id. Emit explicit completion per step. The renderer must keep a skill waiting after its initial tool call returns and accept later progress/terminal events for the same skill run.

## Tasks

- [x] Tool registration and provider-boundary regression.
- [x] Complete mouse primitives, voice tools and strict adapters with behavioral tests.
- [x] Per-step telemetry and visible execution panel with transport and UI tests.
- [x] Verify all contracts with full backend/dashboard checks, provider evidence where available, and visual review against the reference image.
- [x] Audit the original goal requirement by requirement; record the actual scope of runtime evidence and remaining limitations.

Existing uncommitted work stays in the renamed checkout. Backend and renderer work are independent and use the Superpowers subagent-driven-development workflow. No commits or publishing are requested.

## Delivered and verified

All four requested areas are connected in the renamed checkout:

1. Tool access: capability schemas register before connection. Provider-boundary
   tests inspect Google's actual tool configuration and prove repeat capability
   loading does not call `update_tools`. An opt-in live Gemini session selected
   `move_mouse` and `scroll_mouse` with correct arguments; all bodies were mocked
   to isolate tool selection from real desktop input.
2. Hermes features: the preceding milestone's versioned skills, explicit checks,
   persistent evidence, clarification/resume, memory bounds, learning drafts and
   curator suggestions remain covered by the full suite. Tool schemas are now
   eager to suit Gemini's immutable connection; skill bodies/context stay lazy.
3. Mouse operations: voice tools and strict guarded adapters cover movement,
   1–3 clicks, left/right/middle buttons, both scroll axes and bounded dragging.
   Windows dispatch uses real WHEEL/HWHEEL flags and 120 units per notch. Tests
   prove failsafe release and lock retention during caller cancellation. A live
   owned Windows fixture observed click counts, both signed wheel messages,
   pointer destination and button release independently of tool return values.
4. Execution display: Home mounts the panel beside the orb with actual tool
   names, numbered steps, timings, waiting/resume and distinct terminal states.
   Step completion cannot complete the whole run. Long exceptions cannot erase
   terminal metadata by exceeding transport limits. Clear finished is connected.

Final evidence on 18 October 2026:

- Full backend suite with `IRA_RUN_BROWSER_SMOKE=1`: **636 passed, 7 skipped**.
  Includes two real Selenium fixture tests. Skips are four optional persona/live
  tests, the separately run mouse-selection test, the separately run Windows
  desktop fixture, and a Windows symlink-privilege test.
- Live Gemini tool selection (`tests/test_tool_access_live.py`): **1 passed**.
  This tests native-audio model tool selection from typed input, not microphone
  capture or voice interruption behavior.
- Live Windows fixture (`tests/test_skill_desktop_live.py`): **1 passed** after
  the user clicked Start test. The fixture obtains fresh coordinates after that
  click, waits for focus, then performs input only within its own guarded window.
  It verifies text entry, left double-click, right/middle clicks, vertical -3
  notches, horizontal +2 notches, cursor movement, drag and button release.
- Dashboard: **231 tests passed**; typecheck and production build passed.
- Scoped Ruff lint/format and tracked diff whitespace checks passed. Only
  upstream Google SDK deprecation warnings remain in these test runs.
- Independent code review found Windows horizontal scrolling/units and overly
  large terminal telemetry. Both were reproduced, fixed, regression-tested and
  re-reviewed with no outstanding scoped issue.
- Actual Home was visually checked at 1536×1024, 1280×800 and 1000×800 against
  the supplied reference. At compact size, internal scrolling exposes all three
  widgets. Screenshots are `dashboard/execution-preview-desktop.png`,
  `execution-preview-waiting.png`, `execution-preview-compact.png` and
  `execution-preview-compact-widgets.png`. These use synthetic events through
  the actual App/reducer for visual coverage. Temporary QA files/server/browser
  were removed or stopped.

Earlier native attempts exposed fixture focus/coordinate timing: they either
refused input or stopped at observation failure. A Start test handshake now
captures coordinates after user activation; baseline counters exclude the
activation click. The final live pass used all production guards unchanged.

Limits: this proves the owned local Windows fixture, not compatibility with
every application, secondary monitor or DPI setting. Microphone-to-audio
interruption and signed-in Spotify were not exercised in this milestone.
Open Computer Use's experimental Windows MCP runtime remains uninstalled;
selected targeting safeguards were adopted from the pinned-source review.
Autonomous compilation of arbitrary conversations into new skills, automatic
consolidation and model-weight training remain outside the agreed scope.

## Curved display correction — 2026-10-18

The user identified that the execution milestone had replaced the reference's
curved glass panel with a flat card. ExecutionStatus now has a bowed transparent
surface, illuminated rim and receding perspective. Compact numbered steps retain
actual registered tool names, descriptions, outcomes and durations. The panel
remains beside the orb at 1180px, with scrolling/stacking on smaller layouts.

Fresh validation: 231 dashboard tests, typecheck and production build passed.
The actual App was checked at 1536×1024, 1180×800, 960×800 and 760×800, including
collapse, clear, keyboard history scrolling, waiting, offline and disabled animation.
The full reference comparison and focused before/after comparison are saved in
`dashboard/execution-curved-comparison.jpg` and
`dashboard/execution-curved-panel-comparison.jpg`. See `dashboard/design-qa.md`
for dimensions, fixture scope and final findings. Temporary preview code was removed.
