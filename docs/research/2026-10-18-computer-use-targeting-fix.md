# IRA computer-use targeting correction

Date: 18 October 2026. Checkout: `IRA-LiveKit_Agent`, branch `feat/ira-desktop-dashboard`.
Changes remain uncommitted alongside existing work.

## Reproduced failure

The user reported Chrome opening correctly but mouse input missing the desired
profile after a screen capture. The requested target was the top profile named
`kommidi`. The prior live fixture validated native input coordinates, and the
prior Gemini evaluation replaced tool bodies. Neither tested image coordinates
through the provider-to-Windows boundary.

With the installed LiveKit Google encoder, a 1920×1080 capture becomes a
1024×576 image. The previous voice tools passed model `(819,230)` directly to
Windows. The same point in the original capture is `(1536,432)`. This mismatch
was reproduced with the real encoder and a fake physical input boundary.

Live Gemini testing then exposed a separate limitation: both native-audio models
guessed the wrong tile from the synthetic profile image. Instrumentation at
`AsyncSession.send_client_content` confirmed the correctly rendered 1024×576
JPEG was sent. Delivering discrete image context alone did not fix this. The
solution therefore adds observed Windows accessibility targets for named
controls instead of relying on the voice model's pixel estimate.

## Final behavior

- `src/computer_use.py` owns observation IDs, exact image pixel mapping and one
  input per observation. It checks age, window identity/title/bounds, screen
  dimensions, point ownership and fresh UIA target metadata.
- `src/desktop.py` establishes per-thread physical DPI coordinates and restores
  the previous context. The screenshot and input coordinate space must agree.
- `src/desktop_accessibility.py` reads enabled, visible named controls in a
  bounded child process: at most 600 nodes, depth 16, 160 returned targets and
  a five-second process timeout. Password controls are excluded. A new ephemeral
  ID maps to the observed UIA runtime identity, label, role and native bounds.
- `click_desktop_element(target_id, observation_id)` clicks the freshly resolved
  control center. Its metadata is rechecked after indicator startup, followed by
  foreground and hit checks. It cannot reuse an ID from another frame.
- `capture_screen` and each action attach the image and ID together through
  `Agent.update_chat_ctx`, then return exact dimensions and target labels. A
  current realtime session is required before input; fallback changes invalidate
  the previously delivered observation. Other host actions and saved-skill
  adapter calls invalidate it too.
- Mouse/text tools require the latest `observation_id`. Coordinate tools use
  image pixels. Scroll includes the pane's image x/y and positions the pointer
  there first. Saved compiled skills keep native coordinates.
- Post-input window transitions retry only observation, at most three captures.
  Capture/input failures remain unverified; input is never replayed.
- `src/desktop_indicator.py` draws an original blue IRA ring/arrow in a native
  layered, transparent, nonactivating Win32 STATIC window. A child process
  follows the real pointer; pipe closure and bounded lifetime own cleanup.
  No global cursor settings or upstream cursor assets are changed.

The initial Tk overlay prototype passed an immediate focus check but stole
foreground later when its window manager mapped it. A delayed, repeated native
test reproduced this. The final Win32 implementation passed those same checks.

## Verification evidence

1. Offline regression tests initially failed for unscaled coordinates, missing
   delivered observations, pre-validation pointer movement, stale host activity,
   fallback replacement, UIA drift during indicator startup, and post-click
   capture transitions. They pass against the final implementation.
2. Live Gemini evaluation uses the real Assistant and capture/input tools with
   only the physical desktop and UIA provider replaced by an owned profile grid.
   The primary model chose the exact top `kommidi` target ID; the recorded native
   click was `(1420,385)`. Two image messages reached the provider. It did not
   select `kommidi work`. A separate real-tool evaluation covers move and scroll.
   Combined live-provider result: **2 passed**.
3. The native Win32 profile fixture uses genuine accessible BUTTON controls and
   independently records WM_COMMAND. UIA selection and a mapped image click both
   select `kommidi`. The result file is removed before the second click so a
   silent miss cannot reuse old evidence. Captured image: 1024×576; desktop:
   1920×1080; target native bounds: `[579,298,270,120]`; image click: `(380,190)`.
4. Repeated indicator checks include delayed activation, visible window state,
   layered/noactivate/transparent styles, unchanged foreground, underlying hit
   ownership and destruction after an injected failure. Combined final native
   fixture and indicator result: **4 passed**.
5. With the user's actual Chrome picker foreground, IRA's production controller
   observed the unique `Open Kommidi profile` button at native bounds
   `[1193,362,203,224]` and clicked it. Chrome opened a new tab. Independent
   Windows accessibility observation confirmed the window region
   `New Tab - Google Chrome - Kommidi` and profile button `Kommidi`. The immediate
   post-click screenshot crossed the launch transition and correctly reported
   unverified; a fresh read established the result. This prompted the tested
   bounded observation-only retry. This check exercised the native controller;
   the separate Gemini test used the owned grid, not the user's personal screen.
6. Independent read-only review found a UIA revalidation gap after indicator
   startup and a weak second-click assertion. Both were reproduced/fixed and
   re-reviewed. No remaining concrete findings in the scoped review.
7. Final backend suite: **665 passed, 14 skipped**. Skips include opt-in provider
   and native UI checks (run separately above) and environment-specific cases.
   Scoped Ruff lint/format and `git diff --check` passed. One existing Google SDK
   deprecation warning remains in the offline suite; live calls also report the
   SDK's generation-config deprecation. No dashboard code changed in this fix.

## Limits and rollout

Restart IRA and reconnect: Gemini tool schemas are fixed at connection time.
Run `uv sync` on other checkouts to install the Windows-only UIA dependency.
The renamed checkout's old `pytest.exe` shim failed; verification used
`uv run --no-sync python -m pytest`.

Physical input currently covers the primary display. Secondary monitors,
arbitrary canvas manipulation and Unicode typing are not newly supported.
UIA can be unavailable or incomplete for some apps; a screenshot is still
available, but pixel grounding remains a model capability limitation. No claim
of complete Codex feature parity is made. The Open Computer Use MCP runtime
was not installed or embedded. Its design informed observation and input guards.

Sources: [pinned Open Computer Use review](2026-10-18-open-computer-use-adoption.md),
[upstream cursor implementation notes](https://github.com/iFurySt/open-computer-use/blob/5136415f52b87f419227bae28767cf469a6bc3b5/docs/exec-plans/completed/20260417-visual-cursor-overlay.md),
[LiveKit Gemini documentation](https://docs.livekit.io/agents/models/realtime/plugins/gemini/),
and the installed LiveKit 1.8.8 provider/AgentActivity/ImageContent source.
