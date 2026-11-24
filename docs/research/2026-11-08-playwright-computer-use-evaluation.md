# Playwright and computer-use upgrade

IRA now uses native Playwright browser contexts, pages and element handles on
one owner thread. Selenium and its exclusive dependencies are removed from
runtime, lockfile, active skills, scripts and test fixtures. Chrome and Brave
keep their existing IRA profiles. Spotify continues to honor Brave-only preferences.

Computer use keeps one guarded action per observation, refreshes evidence after
input, and recovers from page-control errors with a fresh attached desktop image.
The dashboard shows before/after images, target outlines, pointer/drag markers,
timestamps, activity history and an accessible enlarged view. The native Windows
indicator draws actual target points without intercepting clicks or activating a window.

Unicode typing uses SendInput. A bounded isolated focused-control observer
checks UIA control identity between characters, including controls sharing one
native window. Request and foreground authority are checked again after IPC.
Typed content stays out of action metadata; screenshots remain ephemeral.
Connection changes cancel in-flight preview sends and discard queued frames.

Your Profile and Memory and Context remain under System Controls. The expanded
profile includes location, languages, interests, goals, preferences and About me.
The fixed direct-address rule remains Boss/Sir; saved names do not override it.

## Defects reproduced and repaired

- The owner-thread migration preserves request context and rejects cancelled
  startup or actions. Browser input rechecks URL, identity, eligibility and
  semantic labels after Playwright waits; replaced or changed controls receive no input.
- Chromium canonicalizes time/datetime values. Detached-input normalization
  precedes native fill, avoiding a malformed-value error after an unintended effect.
- Spotify track/playlist metadata is rechecked after actionability waits. A
  1.2-second moving result row reproduced the old 500 ms hover timeout. A bounded
  4-second hover now succeeds, while an artist change during that wait prevents playback.
- The actual Brave page needed 1,862.6 ms for two animation frames. A repeated
  500 ms stable-frame check failed after preparation. Main-document targets now
  receive native Playwright mouse input after a final combined DOM read proves
  connected identity, label/type, enabled state, exact bounds and hit ownership.
  URL/row preflight finishes before that read, with a final request check before
  input. Iframe/shadow targets retain the native element path and its safeguards.
  Real slow-frame and negative overlay/geometry/label/cancellation fixtures pass.
- Browser startup now has an explicit 20-second bound. One actual launch used
  Playwright's 180-second default and outlived the 30-second host deadline;
  request revocation prevented late input, and cleanup settled before retry.
- The initial actual Spotify test selected the song but left an empty player.
  A read-only Widevine probe returned NotSupportedError. Ignoring only Playwright's
  `--disable-component-update` restored DRM support and the enabled player in
  the same signed-in Brave profile. No other media defaults were changed.
- Screenshot queues could survive an interrupted stream/reconnect. Tests now
  verify connection-epoch invalidation, cancellation of the old send, no backlog
  replay, fresh-frame acceptance and listener cleanup.
- Same-window UIA focus changes during text could send later characters into
  another field. Regression tests verify refusal before the next character,
  partial-input reporting, post-query authority checks and helper cleanup.

## Measured verification

| Check | Observed result |
|---|---|
| Backend complete offline suite | 1,517 passed, 83 skipped; 98.53 seconds |
| Dashboard complete suite | 400 passed in 31 files |
| Dashboard TypeScript and Electron production build | Passed |
| Native browser unit subset before final point-read additions | 292 passed, 1 opt-in skipped |
| Final installed Chrome/Brave isolated browser and Spotify DOM checks | 36 passed in 39.17 seconds, including slow-frame trusted input |
| Real Brave moving-row and changed-artist checks | 2 passed; both failed with the prior 500 ms wait |
| Prepared-click/runtime/Spotify regressions | 156 passed; independent prepared/Spotify review also passed 35 cases including four real late-disable checks |
| Desktop/native boundary regression subset | 195 passed after the final focused-query repair; 15 focused tests also independently re-reviewed |
| Windows native editor, focused-control and marker checks | 5 passed in 14.47 seconds |
| Live Gemini/LiveKit isolated browser obstruction recovery | Passed; correct target clicked exactly once |
| Authenticated Spotify in Brave through IRA's scoped tool | Verified Aaya Sher/Anirudh Ravichander and advancing 0:01 → 0:02 playback; 20.01 seconds |
| Electron compositor synthetic visual QA | 7 cases passed, no renderer errors |
| Ruff and installed dependency compatibility | Passed |

The measured live-provider sequence was `browse_to`, `list_page_elements`,
`click_element` (reported obstruction), `capture_screen`,
`click_desktop_element`, `read_page`, `finish_request`. The preview stream emitted
observed, observed, before, after, cleared. The before marker referenced its
accompanying image, and the after image had a new observation ID.

Ten fresh cached UIA focus reads took 2.149 seconds; helper initialization,
field switch and cleanup took 2.999 seconds in that sample. The native test
verified actual Unicode in the intended fields and refused later characters
after a deliberate field switch even with shared native-child identity.
Focused-control reads add measurable latency and remain bounded per query.

The final real Spotify check used the normal scoped tool without diagnostic
overrides. Now-playing title was Aaya Sher; artists included Anirudh Ravichander,
Jangi Reddy, Arjun Chandy and Kasarla Shyam. Duration was 4:46, playing remained
true, and progress advanced from 0:01 to 0:02. The evaluation browser then closed.
The machine-readable result is `2026-11-08-playwright-spotify-evaluation.json`.

Independent final UI and runtime reviews have no remaining actionable findings.
The removal search found no Selenium/browser-driver references in runtime,
dependencies, active skills, scripts, current README or browser test imports.
The explicit old-API absence assertion remains as a migration regression.

## Visual evidence and limits

The Electron screenshots under `dashboard/shots/computer-use-*.png` show an
explicitly labelled synthetic test screen rendered by the actual components.
They verify layout, before/after selection, overlays, enlarged-view focus/escape,
stale and offline states; they are not screenshots of a live voice session.

The provider evaluation uses typed AgentSession input and an isolated physical
backend. It tests real model/tool routing but does not measure microphone
transcription, voice interruption or every possible desktop task. Real Spotify
checks invoke IRA's actual scoped tool separately from model selection.
Network, login, DRM availability and future application changes can still block
a request. Real playlist playback was not evaluated.

The production build is local. Restart the running IRA app/agent and reconnect
to load changed code and tool schemas. Existing sessions were not restarted,
personal data was preserved, and no remote agent was deployed.

## Reproduction

From the repository root:

```powershell
uv run python -m pytest -q -p no:randomly
uv run python -m ruff check src tests scripts
uv run python -m ruff format --check src tests scripts

$env:IRA_RUN_BROWSER_EVALS = '1'
uv run python -m pytest tests/test_playwright_browser_eval.py tests/test_spotify_browser_eval.py -q
Remove-Item Env:IRA_RUN_BROWSER_EVALS

$env:IRA_RUN_LIVE_EVALS = '1'
uv run python -m pytest tests/test_computer_fallback_live_eval.py -q -s
Remove-Item Env:IRA_RUN_LIVE_EVALS

$env:IRA_RUN_DESKTOP_SMOKE = '1'
uv run python -m pytest tests/test_desktop_unicode_live.py -q -s
Remove-Item Env:IRA_RUN_DESKTOP_SMOKE

uv run python scripts/evaluate_spotify_playback.py --track 'Aaya Sher' --artist 'Anirudh Ravichander' --report docs/research/2026-11-08-playwright-spotify-evaluation.json
```

The desktop checks open only a disposable editor; keep input idle while they
run. The Spotify command briefly plays the requested song in IRA's Brave
profile and closes the evaluation browser. From `dashboard/`, run `npm test`,
`npm run typecheck`, and `npm run build`.
