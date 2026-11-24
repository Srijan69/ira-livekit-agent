# IRA execution repair and evaluation — November 7, 2026

IRA's exposed, scoped `spotify_play_track` tool successfully played **Aaya Sher
by Anirudh Ravichander** in **Brave**, using the saved preference **"Spotify:
use Brave only."** This is the version the user selected for the test
(Anirudh Ravichander — The Paradise). The observed player title and artist set
matched, and its position advanced from 0:01 to 0:02. The latest real-host check
took 13.14 seconds, including the independent progress observation.

The [machine-readable playback evidence](2026-11-07-spotify-playback-evaluation.json)
records the tool result and two player observations. The evaluation closes its
browser afterwards; this evidence does not mean the song remains playing.

## What caused the failures

1. **Discovery and execution described different skill systems.** IRA advertised
   natural `SKILL.md` packages but told the model to send those names to the
   optional compiled workflow runner. The default `_skills` store also pointed
   to `SkillCatalog` rather than `SkillStore`. A discoverable procedure could
   therefore fail to resolve or execute through the advertised route.
2. **The Spotify instructions contradicted the actual capabilities.** The prompt
   said there were no dedicated Spotify tools, and the SDK schemas did not expose
   the browser's existing song/player methods. The model could fall back to
   generic browsing or state that it could not play music.
3. **Browser selection and playback evidence were unreliable.** General browsing
   could select Chrome or fall back to the system browser. Spotify's current
   player uses a v2 widget whose title links to an album, while earlier selectors
   expected a track link. JavaScript `.click()` could load a song without the
   trusted input event needed for playback. Seeing any playing audio or a Pause
   label was insufficient to prove that the requested song had started.
4. **A final acknowledgment could close an unfinished request.** The default
   completion mode did not distinguish an unexecuted "I'll play it" from a
   successful result. There was no bounded continuation for a dropped tool call
   or false capability refusal.
5. **JSON repair could alter legitimate content.** Removing trailing commas with
   a broad regular expression also changed comma/bracket text inside strings,
   including nested JSON inputs. Repair now operates outside quoted strings.

The fixes were developed against failing regression cases. Review also found
cancellation gaps during browser opening/search, preference negation errors,
and a compiled dashboard Run request missing its namespace; these were corrected
and covered by tests.

## Hermes behavior adopted

The primary library now follows **compact discovery → full procedure/support
file retrieval → adaptive tool execution → observed result**. IRA reads ordinary
procedures with `skill_view` and performs their steps through its normal LiveKit
tool loop. `run_skill` returns `procedure_ready` for such a package, explicitly
indicating that no action has completed. Optional compiled workflows retain
their separate `SkillStore`, review rules, run history, and execution boundary.

Voice and dashboard discovery identify both modes. Canonical names are
`procedure:category/name` and `workflow:slug`; an ambiguous unqualified name
requires selection. Supporting-file lookup stays within the package boundary,
and platform filtering applies to discovery and retrieval.

A narrow, single continuation can recover a finalized action promise or false
capability refusal when relevant tools exist and no action has occurred. It
retains the same human request identity and never creates a synthetic user
message. It does not replay stopped, stale, interrupted, uncertain, or failed
effects, and does not override clarification or a real blocker. Exhausting the
continuation leaves the request unfinished rather than marking it successful.

Reference implementation: the local extraction at
`C:/Users/kommi/Downloads/hermes-agent-main`, particularly `tools/skills_tool.py`,
`agent/conversation_loop.py`, and `agent/tool_executor.py`. That directory has no
Git revision; this comparison is not tied to a claimed current upstream commit.
SDK integration was checked against LiveKit's [tools documentation](https://docs.livekit.io/agents/logic/tools/)
and [workflows documentation](https://docs.livekit.io/agents/logic/workflows/).

## Spotify and permanent preferences

The SDK now exposes `open_spotify`, `spotify_play_track`,
`spotify_play_playlist`, and `spotify_control`. They read the fresh canonical
profile preference, or legacy user entries when no canonical store is supplied.
General `browse_to` and `new_tab` calls to `open.spotify.com` enforce the same
preference. Negated or contrasted browser names do not override Brave-only;
conflicting exclusive preferences require clarification. Missing Brave and
failed navigation return errors instead of silently substituting another browser.

Song selection matches an exact normalized title and the known artist. Different
artists sharing a title require clarification; duplicate single/album releases
with the same full artist set represent one song for this request. Playback uses
native WebDriver clicks after scrolling and hovering the observed target. When
the player exposes no track URI, verification compares its observed title and
full artist set rather than inventing a URI. Disabled placeholders and a frozen
available playback counter cannot establish success.

Current-request authority is checked before browser launch, switching,
navigation, and later clicks. Stop during opening or search prevents subsequent
play input. Timeout and cancellation classifications retain their original meaning.

The user preference was saved in the canonical profile while preserving its other
fields. IRA's fixed addressing policy uses **Boss/Sir**, and a saved name cannot
override it. **Your Profile** and **Memory and Context** now live in **System
Controls**. The expanded profile includes location, languages, interests, goals,
and About me. Existing profile data and customized memory settings are preserved
during migration; untouched defaults use a 20,000-token application budget and
a 16,000-token summary trigger.

## Verification evidence

| Evaluation | Observed result | What it establishes |
|---|---|---|
| Complete backend suite | **1,372 passed, 48 skipped**, 80.09 s | Offline regressions across execution, profile, memory, bridge, and existing behavior |
| Complete dashboard suite | **365 passed in 28 files** | Shared contracts, System Controls/profile flows, and procedure/workflow UI |
| Dashboard typecheck and production Electron build | **Passed** | Both TypeScript projects and main/preload/renderer build |
| Isolated headless Brave DOM evaluations | **16 passed**; combined browser regression run **42 passed** | Wrong-first-result selection, ambiguity, duplicate releases, remix rejection, v2 metadata, native clicks, placeholder rejection, and idempotent play/pause |
| Gemini Live through LiveKit, final confirmed-artist sample | **4/4 passed**, 27.82 s | Tool routing with relevant schemas and full production schemas, with explicit or saved Brave preference; host effects are simulated |
| Authenticated Spotify in Brave through IRA's scoped tool | **Verified**, 13.14 s | Requested song identity, playing state, and advancing playback position on the real host |
| Ruff on changed Python files and diff whitespace checks | **Passed** | Static checks and patch hygiene |

The live model fixture rejects an unqualified title with multiple artists rather
than pretending that playback succeeded. Diagnostic runs revealed a legitimate
artist clarification for the original title-only command and occasional omitted
artist arguments even with a specified artist. The final sample checks the
confirmed artist and permits normal recovery from the ambiguity error. Four
successful cases are a small sample, not a general reliability estimate.

The backend suite skips opt-in browser/provider evaluations by default; those
were run separately. Earlier negative playlist fixtures needed WebElements and
a scroll result after the native-click change; their assertions still reject
unobserved playback and playback from another playlist.

## Practical limits and loading the changes

- The provider evaluations use typed SDK input and simulated host effects. They
  do not measure microphone capture, spoken transcription, or voice interruption.
  The real-host check invokes the actual exposed tool, independently of model
  routing; it is not an end-to-end spoken-command evaluation.
- Real playlist playback was not evaluated. Its existing playlist-context matcher
  still requires a matching context URI, which current Spotify layouts may not
  expose. IRA must report unverified playlist playback accurately and can inspect
  the browser visually; this report does not certify every playlist.
- Login, network access, browser availability, and future Spotify UI changes can
  still block execution. The validated request is the confirmed Aaya Sher version
  using Brave, not every possible desktop task.
- The production build is saved locally. Restart the running IRA app/agent to
  load the updated tool schemas and execution policy. This work did not restart
  an existing voice session or deploy a remote agent.

## Reproduction

From the repository root:

```powershell
uv run python -m pytest -q

$env:IRA_RUN_BROWSER_EVALS = '1'
uv run python -m pytest tests/test_spotify_browser_eval.py tests/test_browser_and_memory.py -q

$env:IRA_RUN_LIVE_EVALS = '1'
uv run python -m pytest tests/test_spotify_live_eval.py -q

uv run python scripts/evaluate_spotify_playback.py --track 'Aaya Sher' --artist 'Anirudh Ravichander' --report 'docs/research/2026-11-07-spotify-playback-evaluation.json'
```

The live provider command requires the configured Google key. The real-host
command briefly plays music in IRA's persistent Brave profile and closes its
evaluation browser. From `dashboard/`, run `npm test`, `npm run typecheck`, and
`npm run build` to reproduce the frontend checks.
