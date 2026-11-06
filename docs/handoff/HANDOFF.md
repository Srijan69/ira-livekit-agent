# IRA Desktop Dashboard — handoff

**For:** the next coding agent (Codex) · **From:** a Claude Code session · **Date:** 2026-10-15
**Repo:** `C:\Users\kommi\VS code projects\livekit-voice-agent` · **Branch:** `feat/ira-desktop-dashboard`

**Codex continuation, 2026-10-15:** sub-project C and Gemini primary/fallback
integration are implemented in the working tree. See §2 for current verification.
The original A/B history and C acceptance criteria are retained below.

**Desktop shortcut and orb follow-up, 2026-10-15:** `IRA.lnk` is installed in
the Windows Desktop folder (OneDrive on this machine). Recreate it after a build
with `dashboard/scripts/create-shortcut.ps1`; it launches the local Electron
executable directly and uses `dashboard/assets/ira.ico`. It depends on this
checkout, its Python environment, and `dashboard/node_modules`; it is not an
installer. The same icon is used for the app window.

The orb now has blue segmented outer bands, dense ticks, illuminated inner
segments, dark housing and a solid plinth. It scales with stage height to fit
the minimum 1180x800 window. See [reference / before / after](orb-comparison.png)
(browser preview; scripted state). Existing state speeds, HTML-layer rotation,
and reduced-motion behavior are retained.

Checks: 133 Vitest tests, TypeScript and production build pass. Browser checks
covered 1536x1024 and 1180x800, reduced-motion freezing, and renderer errors.
Launching the actual desktop shortcut started the bridge and agent with zero
restarts, reached Online, attached the voice element and showed no error banner.
Opening it again reused the running app. Spoken response quality and model
failover were not evaluated in this visual/launcher follow-up.

**Supplied background and glass styling, verified 2026-10-16:** the Home scene
now uses the user's `Futuristic Command Deck Overlooking Neon City.png`, bundled
unchanged as `dashboard/assets/command-deck.png`. This supersedes the original
procedural-background decision in Appendix Section 2. The sidebar, widget rail,
action cards and command bar use perspective and glass styling closer to the
reference. The approved orb stays animated and aligns with the image's platform;
its separate drawn base is hidden on Home to avoid duplicating the platform.

Latest [background preview](current-home-background.png) is a 1536x1024 browser
capture with a scripted session; system stats and tasks require the desktop app.
The 1180x800 and 1180x1024 layouts were also visually checked. Chat/Home navigation,
command submission and microphone toggling work in the preview, with no browser
errors or horizontal overflow. All 133 Vitest tests, TypeScript checks and the
production build pass. The emitted background matches the original SHA256, and
the production renderer uses the live session. The existing desktop shortcut
targets this rebuilt app; real voice was not relaunched in this final check.

Read [`AGENTS.md`](../../AGENTS.md) first — it is binding for this repo (uv, ruff, test-driven
changes to agent behaviour, and tests that never touch the real desktop, browser, database or
network). Then read this file top to bottom before changing anything.

---

## 0. TL;DR

- **Goal:** turn [the reference image](reference-dashboard.webp) into a real Windows desktop
  app (Electron + React) wired to the existing Python LiveKit voice agent, **IRA**. The user
  wants it to look like the image ("same to same"), animate smoothly, and have a working
  backend. Their original plan is [original-plan.md](original-plan.md); the agreed design
  below supersedes it where they differ.
- The work is split into four sub-projects:

  | | Sub-project | Status |
  |---|---|---|
  | **A** | Electron shell + Home page UI + animation | ✅ done |
  | **B** | Voice spine: token bridge, dispatch, typed commands, live orb state | ✅ done, verified on the real app |
  | **C** | Live widgets: tasks (SQLite + voice tools), weather, IRA's `system_status` tool | ✅ implemented and verified offline — see §2 |
  | **D** | Presence + packaging: tray, global hotkey, autostart, installer | later — see §7.2 |

- Current visual preview: [current-home-background.png](current-home-background.png),
  with the user's supplied scene and a scripted session.
- Earlier widget preview: [current-home-widgets.png](current-home-widgets.png), using
  sample data and a scripted session. The earlier real-voice screenshot remains
  [current-home-speaking.png](current-home-speaking.png).
- Tests: **133 Vitest** and **306 Python** pass; **4 live Gemini evals skip by default**.
  TypeScript, Ruff and the production build pass. Live voice/provider failover
  was not revalidated in this continuation.

---

## 1. Decisions already made with the user — do not relitigate

| # | Decision | Why |
|---|---|---|
| 1 | **IRA is the single brain; Electron is a thin client.** Clicking "Open VS Code" sends the *text* to IRA over LiveKit and her existing `open_app` tool does it. No Python tool is reimplemented in JavaScript. | One code path, already tested, and IRA knows what happened. |
| 2 | **Architecture "A" (hybrid):** conversation and realtime state go over the LiveKit room; stored, queryable data goes over a loopback HTTP bridge (`src/bridge.py`); weather is fetched by the renderer straight from Open-Meteo. | The panels keep working when the agent is down — a crashed agent degrades voice only. |
| 3 | **System stats by consumer:** the rings use Electron's `systeminformation`; IRA's spoken answers will use Python's `psutil` (§7.1, C6). The duplication is deliberate. | Rings work before any session exists; IRA can answer with no dashboard open. |
| 4 | **Home page only, for now.** The other eight sidebar items render a labelled "Not built yet" placeholder. Each future page gets its own design pass with the user. | User's call. |
| 5 | **Match the reference layout, but no borrowed character.** No Iron Man art and no "Tony Stark" quote. The left panel shows abstract line art and *"The answer first, boss. Always." — IRA*. | `tests/test_agent.py` already asserts IRA's prompt never names a borrowed character; the UI follows the same rule. |
| 6 | **"Good Evening, Boss" — not "Sir".** | IRA's persona addresses the user as "boss", and a test enforces it. |
| 7 | **Tasks live in Python SQLite** (a new table in `ira_memory.db`), with voice tools. | User: "use whatever is fast and reliable." It also lets IRA create and read tasks. |
| 8 | **Electron supervises both Python processes** (the bridge, and the agent in `dev` mode). | Double-clicking the app starts everything. |
| 9 | **Target OS: Windows** (the user is on Windows 11). | |
| 10 | The reference reads "Tue, 30 Sep 2026", but **30 Sep 2026 is a Wednesday.** Match the *format*, not the error. | A clock that disagrees with the calendar is worse than one that disagrees with a mockup. |

---

## 2. Current state

### Commits on `feat/ira-desktop-dashboard`

| Commit | What |
|---|---|
| `a73d804` | Baseline: the voice agent before any dashboard work (the repo was not under git before this session) |
| `3d33d5e` | Sub-project A: Electron shell and animated Home page |
| `d8182e8` | First fidelity pass: proportions closer to the reference |
| `883edd2` | Sub-project B: the voice spine |
| `58fc33b` | Orb off-centre rotation fix + second fidelity pass |

Nothing is pushed; there is no remote.

### Sub-project A — done

The Home page matches the reference's layout: sidebar, header (clock, greeting, Online badge,
mic / minimize / close), the animated orb with six live cue words (THINK, ANALYZE, RESPOND,
UNDERSTAND, LEARN, EXECUTE), a waveform, System Status rings, Weather, Upcoming Tasks, seven
quick-action cards, the command bar and suggestion chips.

- **Real:** window chrome and dragging, navigation, clock and greeting, the orb's state machine
  and animation, live system stats.
- C removed `dashboard/src/lib/mock.ts`: weather and tasks now load real data.
  The name/tier are explicit local display defaults in `dashboard/src/lib/profile.ts`.

### Sub-project B — done

- **`src/bridge.py`** — aiohttp on 127.0.0.1. `POST /token` mints a JWT with IRA's dispatch
  baked in; `GET /health` reports which keys are configured, as booleans only. Every request
  except `/health` needs a per-launch shared secret.
- **`src/agent.py`** — `text_input=True`, so typed text arrives as an ordinary user turn. A
  status publisher mirrors `SessionStatusReporter` onto the `ira.status` data topic (ordered,
  reliable, held until the room connects), and tool events now carry the tool's name.
- **`src/telemetry.py`** — `SessionStatusReporter(observer=...)`, a structured second sink; the
  terminal output is unchanged.
- **Electron main** — a `ManagedProcess` supervisor for both Python processes, free-port
  selection, a bridge proxy (the secret never reaches the renderer), and microphone-only
  permissions.
- **Renderer** — `SessionController` owns the connection rules, `useLiveKitSession` adapts it
  to React, `AudioMeter` plays IRA and measures loudness for the waveform, `statusTracker`
  follows tool calls, and `StatusBanner` names failures.

### Sub-project C and model migration — implemented

- **C1–C3:** explicit SQLite busy timeout; lazy, locked `TaskStore`; authenticated
  bridge CRUD with UTC dates, 500-character titles, limits of 1–100 and 400/404 errors.
  Bridge and agent use the same `IRA_MEMORY_DB` and close their stores on shutdown.
- **C4:** `current_time`, `add_task`, `list_tasks`, `complete_task`, reliable
  `ira.events` invalidation, isolated simulation mocks and a task scenario.
  User chose a current-time tool for relative dates. Tasks are saved to-dos, not alarms.
- **C5:** named task IPC, secret kept in main, task hook and real Home list.
  Eight upcoming tasks by default; View All requests up to 100. Optimistic
  completion rolls back on failure. Refreshes follow voice writes, room reconnect
  and bridge readiness. Only reliable events from room agent participants qualify.
- **C6:** `system_status` uses injected psutil, with explicit dependency and a short
  CPU sample off the event loop. Missing batteries return null.
- **C7:** Open-Meteo current data, WMO conditions, day/night icons, per-location
  cache and 15-minute visible polling. User chose Hyderabad by default with an
  editable city search. Offline refreshes retain the last good reading and its age.
- **C8:** all quick actions ask one focused question before helping, as requested.
  Attachments remain disabled. README covers startup and the unfinished pages.
- **Models:** `gemini-3.8-live` primary, `gemini-2.5-flash-native-audio-latest`
  fallback. Both use Google AI Studio's Developer API, Kore and direct audio.
  Optional `IRA_GEMINI_PRIMARY_MODEL` / `IRA_GEMINI_FALLBACK_MODEL` overrides
  accept compatible Live models. Extended Thinking is excluded pending its
  separate interaction/tool lifecycle. No STT/TTS was introduced.
- **Failover:** LiveKit preserves context, instructions and tools; interrupted
  user actions are never automatically replayed. Only the tools-disabled startup
  greeting retries once after a model swap. Exhaustion surfaces an error.
  `src/realtime_model.py` contains one SDK-private compatibility shim for
  generation-future failures, tested against the locked LiveKit 1.8.8 release.

**Verification (2026-10-15):** 306 Python tests passed, four live evals skipped;
133 Vitest tests passed; both TypeScript configs, Ruff lint/format, and Electron
production build passed. One upstream `google.genai` deprecation warning remains.
Browser QA at 1440×900 used fake task IPC, weather and system readings: expanded
list, successful save, save rollback, city selection/persistence, stale weather,
and quick-action text were checked. No page errors or horizontal overflow remained.
SQLite durability and voice-to-panel invalidation were verified through isolated
tests, not a real voice session. No personal database or microphone was used.

**Still unverified:** live access on this Google key, spoken response quality,
live provider failover and the cloud simulation scenario. The installed `lk`
2.13.2 is too old for current docs/simulation commands; upgrade with
`winget upgrade LiveKit.LiveKitCLI`. Documentation was fetched from public Google,
LiveKit and Open-Meteo URLs. D and other sidebar pages remain out of scope.

### Historical B verification on the real app (2026-10-15, before model migration)

Run with `npx electron .` plus `scripts/e2e-probe.cjs` (see §3):

- Bridge ready at +2.1 s → agent registered at +6.8 s → room joined, badge **Online** at
  +9.3 s → mic on → IRA's audio track attached → greeting *"Hello, boss."* with **RESPOND** lit
  → back to LISTENING.
- A prompt typed into the real command bar (*"Reply with just the word ready, nothing else."*)
  appears in the agent log as a user turn, and she replies *"ready"*.
- Exactly one dispatch per session; both Python children stop when the app quits.

---

## 3. How to run

Prerequisites: Node (developed on v24.14.1), `uv` on `PATH`, and `.env.local` at the repo root containing
`LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` and `GOOGLE_API_KEY` (it already exists
on this machine — **never print or commit it**).

```bash
cd dashboard
npm install
npm run dev          # Electron + live IRA. Supervises the bridge and the agent itself.
```

- **Agent in your own terminal**, to read its logs: run `uv run python src/agent.py dev` at the
  repo root, then start the app with `IRA_EXTERNAL_AGENT=1 npm run dev` (PowerShell:
  `$env:IRA_EXTERNAL_AGENT="1"; npm run dev`).
- **Browser preview, no voice:** `npm run dev:web`, then open <http://localhost:5273>. It runs a
  scripted mock session so the orb walks through every state — the quickest loop for UI work.
- **Production build:** `npm run build`, then `npm start`.

Heads-up: the live app opens the microphone and IRA speaks through the speakers.

---

## 4. How to test

```bash
# Renderer + supervisor (Vitest), both tsconfigs
cd dashboard && npm test && npm run typecheck

# Python (repo root); live provider evals skip unless explicitly enabled.
uv run pytest -q -p no:randomly
uv run ruff check src tests
uv run ruff format --check src tests
```

- **Live evals are opt-in:** set `IRA_RUN_LIVE_EVALS=1` and provide `GOOGLE_API_KEY`.
  Earlier B-era runs timed out with `generate_reply timed out waiting for
  generation_created event`, including on baseline `a73d804`. Do not treat those
  historical runs as validation of the new model pair.
- **Visual check** (renderer only, scripted session):
  ```bash
  cd dashboard
  VITE_IRA_MOCK=1 npx electron-vite build
  npx electron scripts/screenshot.cjs    # writes dashboard/shots/*.png (gitignored)
  npm run build                          # restore the real build afterwards
  ```
- **Full-stack check** (real supervisor, bridge and agent; IRA speaks; the probe types one
  harmless prompt):
  ```bash
  cd dashboard && npm run build
  IRA_E2E_PROBE="$(pwd -W)/scripts/e2e-probe.cjs" IRA_E2E_SECONDS=60 npx electron .
  ```
  (`pwd -W` is Git Bash; in PowerShell give the probe's absolute path.)
  It logs every supervisor transition and what the window shows, saves screenshots and the last
  agent log lines to `dashboard/shots/`, then quits. The hook in `electron/main.ts` is ignored in
  packaged builds.

---

## 5. Architecture and contracts

```
Electron main ──spawns──▶ uv run python src/bridge.py --port <free>   (IRA_BRIDGE_SECRET in env)
   │          ──spawns──▶ uv run python src/agent.py dev               (LiveKit worker "my-agent")
   │ IPC (contextBridge, named operations only)
   ▼
Renderer ──IPC──▶ main ──HTTP + secret──▶ bridge: /token, /health, /tasks
   │
   └──WebRTC──▶ LiveKit Cloud ◀── agent worker, summoned by the dispatch inside the token
```

### Files

| Path | Role |
|---|---|
| `src/bridge.py` | Token minting with embedded dispatch, `/health`, shared-secret check |
| `src/agent.py` | `_room_options()` (`text_input=True`), `_room_status_publisher()`, `STATUS_TOPIC`, `STATUS_BACKLOG`, the tool-name-aware `_attach_status_reporter()` |
| `src/telemetry.py` | `SessionStatusReporter`, with an optional `observer` |
| `dashboard/electron/main.ts` | Window, IPC, supervision, bridge proxy, permissions, e2e hook |
| `dashboard/electron/supervisor.ts` | `ManagedProcess`: ready patterns, bounded restarts, tree kill, UTF-8 env |
| `dashboard/electron/system.ts` | `SystemStatsPoller` (2 s fast / 10 s slow; paused on blur, minimize and hide — see §7.4) |
| `dashboard/electron/preload.ts` | The entire renderer API (`window.ira`) |
| `dashboard/shared/types.ts` | Every type that crosses the main/renderer boundary |
| `dashboard/src/lib/orbState.ts` | Pure orb state machine and cue logic |
| `dashboard/src/lib/sessionController.ts` | Connection rules, retries, watchdogs (no React) |
| `dashboard/src/lib/statusTracker.ts` | Keeps the in-flight tool separate from conversational chatter |
| `dashboard/src/lib/audioMeter.ts` | IRA's playback element + loudness taps for the waveform |
| `dashboard/src/hooks/useSession.ts` | Chooses the live or mock session at module load (`VITE_IRA_MOCK`) |
| `dashboard/src/components/IraOrb.tsx` | The orb; each rotating ring is its own HTML layer |
| `dashboard/src/components/StatusBanner.tsx` | Names failures; offers "Restart IRA" |
| `dashboard/src/styles/theme.css` | Design tokens — retune the whole look here |

### Bridge HTTP (loopback only)

- `POST /token` with body `{identity?, room?}` → `{url, room, token}`. Returns **401** without
  the `X-IRA-Bridge-Secret` header (or with an empty configured secret — it fails closed),
  **503** naming the missing LiveKit variable, and **400** for a room or identity over 120
  characters.
- The token: identity `boss`, 1 h TTL, grants `roomJoin`, `canPublish`, `canSubscribe` and
  `canPublishData` only, plus `roomConfig.agents = [{agentName: "my-agent"}]`.
- `GET /health` → `{bridge: "ok", config: {livekit_url, api_key, api_secret, google_api_key}}`,
  all booleans. Deliberately reachable without the secret.
- Task routes require the secret: `GET /tasks?limit=8` → `{tasks:[...]}`;
  `POST /tasks {title,due_at?}` → 201 `{task:...}`;
  `PATCH /tasks/{id} {done}` → 200 `{task:...}`;
  `DELETE /tasks/{id}` → 200 `{deleted:true}`. IDs cross JSON as strings.
  Invalid input returns 400; a missing task returns 404. Dates require an explicit
  timezone and normalize to UTC `Z`. Completed rows are excluded from upcoming.

### LiveKit

| Channel | Direction | Content |
|---|---|---|
| text stream, topic `lk.chat` | UI → IRA | Command bar, quick actions, chips. The SDK's handler interrupts her and replies, exactly as if spoken. |
| data, topic `ira.status` | IRA → UI | JSON `{run_id, label, state, detail, at}`. States: `LISTENING`, `RECOGNIZING`, `GENERATING`, `SPEAKING`, `DELEGATING <tool>`, `RUNNING <progress>`, `VERIFYING`, `DONE <tool>`, `ERROR <tool>`. |
| data, topic `ira.events` | IRA → UI | Reliable `{type:"tasks.changed"}` after a task write; the panel refetches records through the bridge. |
| participant attribute `lk.agent.state` | IRA → UI | `initializing` / `idle` / `listening` / `thinking` / `speaking` |
| text stream, topic `lk.transcription` | IRA → UI | The SDK's transcripts — not consumed yet (a future Chat page) |

### IPC channels (`dashboard/electron/main.ts`)

`ira:stats` (push), `ira:stats:snapshot`, `ira:window:minimize`, `ira:window:close`,
`ira:window:toggle-always-on-top`, `ira:session:token`, `ira:health`, `ira:agent:status`,
`ira:agent:status-push` (push), `ira:agent:restart`, `ira:agent:logs`.

C adds `ira:tasks:list`, `ira:tasks:create`, `ira:tasks:update`, `ira:tasks:delete`.

### Readiness signals and environment variables

- The agent is **ready** when it prints the SDK's `registered worker`; the bridge when it prints
  `bridge listening` (emitted after the socket is bound).
- Environment variables: `IRA_BRIDGE_SECRET` (set by Electron), `IRA_REPO_ROOT` (overrides the
  Python project location), `IRA_EXTERNAL_AGENT`, `IRA_AGENT_NAME` (bridge; default
  `my-agent`), `VITE_IRA_MOCK`, and `IRA_E2E_PROBE` / `IRA_E2E_SECONDS` / `IRA_E2E_PROMPT_AT`.

---

## 6. Hard-won gotchas — read before touching these areas

1. **Explicit dispatch has a timing trap.** A room's dispatch is created when the room is
   joined. Join before IRA's worker has registered and the dispatch has nowhere to go, so she
   never arrives. `SessionController` waits for the agent's `ready` state (45 s fallback) and,
   when IRA restarts, moves to a *fresh* room rather than waiting in the old one.
2. **Motion + SVG transforms.** Motion puts any SVG element it transforms in
   `transform-box: fill-box`, which makes a pixel `transform-origin` relative to that group's own
   bounding box. The rings rotated up to 82 px off-centre. **Rotate HTML layers, never SVG
   groups** (`Layer` in `IraOrb.tsx`; pinned by `IraOrb.test.tsx`). HTML layers also composite
   on the GPU, so glow filters inside them cost nothing per frame.
3. **Changing a Motion animation's `duration` restarts it**, which snaps the rings back to zero.
   Rotation runs through `useSpinner` (a frame loop with an eased speed); breathing is a CSS
   keyframe driven by a CSS custom property, for the same reason.
4. **Container queries measure the content box.** Padding on an `@container` element silently
   shrinks every threshold (see the comment in `Stage.tsx`). And viewport breakpoints (`xl:`)
   are wrong for elements inside a constrained column — they overflowed and pushed the right
   rail off screen.
5. **Python children on Windows:** piped stdout falls back to cp1252 and the LiveKit SDK crashes
   printing its own emoji. `ManagedProcess.childEnv` forces `PYTHONUTF8=1`,
   `PYTHONIOENCODING=utf-8` and `PYTHONUNBUFFERED=1` (the last so the ready line arrives on time).
6. **Never `shell: true`** for supervised children. The `cmd.exe` wrapper orphans the process
   tree on kill and breaks paths containing spaces. Kill with a synchronous `taskkill /T /F`.
7. **A restarted process's old `exit` event arrives after its replacement has spawned.** Every
   handler checks that it still belongs to the current child, or a restart spawns a second IRA.
8. **In the Python SDK, `room.local_participant` raises until the room is connected**, and the
   entrypoint emits status before `ctx.connect()`. The publisher holds events in a bounded
   backlog and flushes them in order.
9. **The status stream must be ordered and reliable:** the UI pairs each tool's `DELEGATING` with
   its `DONE`, and nothing corrects a lost or reordered `DONE`.
10. **asyncio keeps only weak references to tasks** — the publisher holds strong references in
    an `in_flight` set.
11. **Electron grants every permission by default.** `restrictPermissions()` allows audio only.
12. **CSP** (in `dashboard/src/index.html`): LiveKit needs both `wss://*.livekit.cloud` and
    `https://*.livekit.cloud`. Open-Meteo is already allowed.
13. **React-controlled inputs in automation:** set the value through the native setter and
    dispatch an `input` event, or React never sees it (see `e2e-probe.cjs`). Tailwind's
    arbitrary class names (`text-[12px]`) need escaping in `querySelector`, so the probe matches
    elements by visible text instead.
14. **`psutil` is now an explicit dependency.** CPU sampling is off the event loop;
    a short blocking sample avoids a fresh worker thread's empty CPU baseline.
15. **The `lk` CLI here is 2.13.2**; `lk docs` needs 2.15.0 or newer
    (`winget upgrade LiveKit.LiveKitCLI`). AGENTS.md asks you to use it for LiveKit docs and to
    file docs feedback.
16. **TypeScript is pinned to `^5.9.3` on purpose** — 7.x is the Go rewrite. **`@livekit/components-react`
    is deliberately not used**; the orb's state machine takes plain session facts.

---

## 7. Next work, in order

### 7.1 Sub-project C — completed acceptance criteria (historical specification)

Write tests first throughout (AGENTS.md). Each step lists what "done" means.

**C1. Fix SQLite contention — do this first.** `src/memory.py`'s `connect()` enables WAL but
sets no `busy_timeout`. Once the bridge (a second process) writes `ira_memory.db`, two concurrent
writers raise `database is locked` immediately instead of waiting. Add `PRAGMA busy_timeout=5000`
to every connection, and correct the comment above `check_same_thread=False`, which says access
is serialized — true for one process only.
*Done:* a test with two connections writing from two threads raises no `OperationalError`.

**C2. `src/tasks.py` — `TaskStore`**, in the style of `MemoryStore` (lazy connect, WAL,
`busy_timeout`, `IRA_MEMORY_DB`). Schema:
`tasks(id INTEGER PRIMARY KEY, title TEXT NOT NULL, due_at TEXT NULL, done INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)`,
with `due_at` as ISO-8601 **UTC**. Methods: `add(title, due_at)`, `upcoming(limit=8)` (not done,
ordered by `due_at`, nulls last), `set_done(id, done)`, `delete(id)`. Cap the title length (the
memory store caps facts at 500).
*Done:* `tmp_path` tests cover ordering, nulls, toggling, deletion, the cap and UTC storage.

**C3. Bridge routes:** `GET /tasks?limit=8`, `POST /tasks {title, due_at?}`,
`PATCH /tasks/{id} {done}`, `DELETE /tasks/{id}` — all behind the secret, with validated input
(title length, parseable ISO date) and 404 for unknown ids. Inject the store through
`create_app(config, tasks=...)`. Suggested JSON shape:
`{"tasks":[{"id":"12","title":"…","due_at":"2026-10-16T03:30:00Z","done":false,"created_at":"…"}]}`.
*Done:* `aiohttp.test_utils.TestClient` tests, in the style of `tests/test_bridge.py`.

**C4. Agent tools:** `add_task`, `list_tasks`, `complete_task`, plus a short `# Tasks` section in
`_INSTRUCTIONS` (keep it short — prompt size is latency). Confirm tersely ("Added, boss."). After
any write, publish `{"type":"tasks.changed"}` on a new reliable data topic, `ira.events`. Add a
scenario to `scenarios.yaml` ("add a task by voice"), mocking the tool in the entrypoint behind
`ctx.simulation_context()` the way `open_app` already is.
**Resolved: user chose (b), `current_time`.** Original question: IRA does not know the current date, so she cannot turn "tomorrow
at nine" into a timestamp. Options: (a) put the date in the instructions at session start (cheap,
but frozen for the whole session), (b) a tiny `current_time` tool, (c) parse natural language in
the tool with `dateparser` (a new dependency). Recommendation: (b).
*Done:* tool tests with a `tmp_path` store; registration test; live eval optional.

**C5. Wire the panel.** Main proxies `ira:tasks:list | create | update | delete` to the bridge
with the secret (copy the `ira:session:token` pattern); preload exposes `tasks.*`; a renderer
`useTasks()` fetches on mount, refetches on `ira.events` → `tasks.changed` (this needs a small
generic event hook in `SessionController`), and toggles optimistically. Replace `MOCK_TASKS` in
`App.tsx`, and move the `Task` and `Weather` types out of `mock.ts` (into `shared/types.ts` or a
renderer types file) before deleting it. "View All" is currently inert.
*Done:* ticking a task in the UI persists across a restart, and a task added by voice appears
without a reload.

**C6. `src/system_status.py` + a `system_status` voice tool.** Use `psutil` (declare it — gotcha
14), injected through a loader the way `DesktopController` does it. Prime
`cpu_percent(interval=None)` at construction, since its first call returns 0. Speak numbers the
persona's way ("Twelve percent, boss") and handle a desktop PC with no battery.
*Done:* tests with a fake psutil module.

**C7. Real weather.** A renderer hook calling Open-Meteo's forecast endpoint for current
temperature, apparent temperature, humidity, wind, weather code and day/night (**verify the exact
parameter names against Open-Meteo's docs first**). Map WMO weather codes to the `condition`
values `WeatherCard` understands, and `is_day` to `isNight`. Cache the last good reading in
`localStorage`, refresh every 15 minutes, and pause while hidden. `WeatherCard` already shows
"N m ago" for stale data. Location: the reference shows Hyderabad, India (17.385, 78.4867) —
default to that. **Resolved: user requested an editable location control.**
*Done:* real numbers, and a stale card rather than an empty one when offline.

**C8. Leftovers from A and B.** **Resolved: user chose one focused question, then help.**
The original quick-action prompts in `lib/nav.ts` were placeholders
("Search the web for me.") — refine them with the user. The paperclip button is disabled until a
file-handoff design exists. The user's name and tier come from `MOCK_USER` until a Settings page
exists. The README has no dashboard section yet — add one (how to run, what's mocked).

### 7.2 Sub-project D — presence and packaging

- Tray icon and menu (Show/Hide, Restart IRA, Quit); a global `Ctrl+Space` shortcut to toggle the
  window; an always-on-top control in the UI (the `ira:window:toggle-always-on-top` IPC already
  exists, unused); launch at login. Ask the user whether closing the window should quit or hide
  to tray.
- **Packaging with electron-builder — the hard part is finding the Python project and `uv` from an
  installed app.** `repoRoot()` in `electron/main.ts` is `dirname(app.getAppPath())`, which only
  holds in development (`IRA_REPO_ROOT` overrides it). Either point the packaged app at the
  user's checkout through a setting, or bundle `src/` and a uv-managed environment as
  `extraResources`. Decide with the user.
- An Electron launch smoke test (Playwright's `_electron`) belongs here.
- **Measure idle CPU.** The design promised under 2% renderer CPU while idle-listening; it has
  never been measured.

### 7.3 The other pages — each needs its own design pass with the user first

Chat (transcripts are already published on `lk.transcription`), Voice, Tasks (full CRUD on top
of C), Automation (the `src/workflows.py` library), System Control, Apps (a grid that sends
"open X" to IRA), Research, Settings (name, location, hotkey).

### 7.4 Known issues — not investigated

- The agent log shows "event loop blocked for 100–250 ms" warnings mid-conversation. The startup
  ones (SSL context creation, imports) predate this work; the mid-conversation ones have not been
  investigated.
- `ManagedProcess` keeps only the last 200 log lines, and nothing in the UI displays them yet
  (`ira:agent:logs` exists). The failure banner shows only the exit reason.
- "Restart IRA" restarts both the bridge and the agent.
- **The stats poller pauses on window *blur*, not just on hide/minimize** (`bindVisibility` in
  `electron/main.ts`). The rings freeze whenever the dashboard is visible but unfocused — e.g.
  on a second monitor. The design only asked to pause while hidden; dropping the `blur`/`focus`
  pair is probably right, but confirm with the user.
- Rooms are never deleted explicitly; LiveKit's empty-room timeout cleans them up.
- From `ROADMAP.md`'s security section, still true: voice input has no authentication. The typed
  path is narrower — it needs the unlocked machine.

---

## 8. Working rules for this repo

- **AGENTS.md is binding.** Python through `uv`; `uv run ruff format` and `uv run ruff check`;
  tests before behaviour changes; tests use fakes, `tmp_path` and fake rooms — never the real
  desktop, browser, memory database, network or LiveKit.
- **Never add an STT or TTS stage**, and keep `GEMINI_LIVE_MODEL` a native-audio model:
  `generate_reply()` powers the greeting and the in-process evals. See `ROADMAP.md`'s anti-goals.
- **Renderer security invariants:** `contextIsolation` on, `sandbox` on, `nodeIntegration` off;
  the preload exposes named operations only; the bridge secret stays in main; keep the CSP tight;
  the permission handler allows audio only.
- **Animation:** per frame, animate only `transform` and `opacity`; rotate HTML layers, not SVG
  groups; stop loops while the window is hidden; honour `prefers-reduced-motion`.
- **Visual changes:** compare against `reference-dashboard.webp`, and refresh
  `current-home-background.png` when the look changes meaningfully. Preserve the
  earlier `current-home-speaking.png` as the real-voice verification capture.
- **C questions are answered:** current-time tool (C4), editable Hyderabad-default
  weather (C7), one focused quick-action question (C8). Do not ask again.
  Close-to-tray and packaging decisions remain open for D.

---

## Appendix — the agreed design, as referenced in code comments

The design was agreed with the user in conversation, section by section, and never written to a
file (the user asked to go straight to building). Code comments cite it — "Section 3's rule",
"Section 5's table" — so here it is, condensed.

**Section 1 — Architecture.** One repo, three processes, Electron owns all three lifecycles (§5
diagram). Additive only: nothing in the existing agent changes shape. `dashboard/` lives in this
repo so the bridge contract and its consumer change in one commit; the `Dockerfile` and the
LiveKit Cloud deploy are untouched (the bridge is never their entrypoint). `telemetry.py` grows a
second sink rather than a parallel state system.

**Section 2 — Home layout.** CSS grid: full-height sidebar; header (logo, clock, centred
greeting, badge and window controls); stage (quote panel, cue columns, orb, caption, waveform);
right rail (System Status, Weather, Upcoming Tasks); a bottom block (quick actions, command bar,
chips) centred under both the stage and the rail. Design tokens live in `styles/theme.css`. The
background is generated (gradient + SVG skyline + window mullions), never the screenshot. Fonts
are bundled (`@fontsource/inter`, Latin subsets only), never a CDN. The six cue words are live,
not decoration (`activeCuesFrom` in `lib/orbState.ts`).

**Section 3 — Orb and animation.** Rings are SVG drawn into HTML layers; the waveform is one
canvas `requestAnimationFrame` loop. **The rule: per frame, animate only `transform` and
`opacity`** — the status rings' `stroke-dashoffset` is the single exception, firing on a 2 s stat
update rather than per frame. Orb states and behaviour:

| State | Behaviour | Caption |
|---|---|---|
| `offline` | rings dim to ~30 %, rotation nearly stops, core greys | OFFLINE |
| `connecting` | rings fade in, rotation spins up | CONNECTING... |
| `listening` | counter-rotation (outer 60 s/rev, mid 40 s reverse, inner 24 s) + breathing 1.00↔1.03 over 4 s | LISTENING... |
| `recognizing` | waveform follows the **mic**; ANALYZE and UNDERSTAND light | LISTENING... |
| `generating` | rotation ×2.5; THINK lights | THINKING... |
| `speaking` | waveform follows **IRA's audio**; RESPOND lights | SPEAKING... |
| `executing` | EXECUTE lights (LEARN too for memory-writing tools) | WORKING... |
| `error` | a 1.5 s amber flash, then back to the prior state | (keeps the prior caption) |

Transitions ease over ~400 ms with no hard cuts. Every loop stops while the window is hidden, and
`prefers-reduced-motion` stops rotation while keeping the cross-fades. Target: **under 2 %
renderer CPU while idle-listening** (not yet measured). Entrances stagger at ~60 ms per panel.

**Section 4 — Data contracts.** As in §5 above. The data channel carries *notifications, never
records*: when IRA changes a task she emits `tasks.changed` and the panel refetches over HTTP, so
there is one serialization of a task. Due dates are stored in UTC and formatted at render time,
so an overnight dashboard never calls this morning "Tomorrow". The badge reports IRA actually
being in the room; `/health` answers *why* she isn't.

**Section 5 — Failure handling and security.** Every failure degrades one thing, not the app:

| Failure | Behaviour |
|---|---|
| Agent process exits | Restart with backoff 1 s → 2 s → 4 s, then stop and show the reason ("IRA stopped: …") with a Restart button. Stats, weather and tasks keep working. |
| Bridge port taken | Main picks a free port by bind-testing and passes it as `--port`. |
| Missing key (`GOOGLE_API_KEY`, LiveKit) | `/health` names it; the banner says "Not configured: X". |
| Token fetch or LiveKit unreachable | Badge shows the state, retries with backoff, the banner says which. |
| IRA joins but never speaks | After ~15 s, a hint pointing at `GOOGLE_API_KEY` / `GEMINI_LIVE_MODEL`. |
| Tool error | `ERROR` status → amber flash; the orb returns to its prior state. |
| Open-Meteo down | Last good reading with its age — never an empty card (C7). |
| Concurrent database writes | `busy_timeout` on every connection (C1). |

Security: a per-launch shared secret on the bridge (loopback stops the network, not other local
processes, and a token is the ability to talk to IRA); the API secret never leaves Python; the
renderer sees only JWTs; tight CSP; tokens never logged.

**Section 6 — Testing.** Python: `TaskStore` on `tmp_path`; the bridge through
`aiohttp.test_utils.TestClient`, including the load-bearing check that the minted JWT carries
`RoomAgentDispatch(agent_name="my-agent")`; `system_status` with a fake `psutil`; agent behaviour
tests first. Renderer: the orb state machine as a pure function; date formatting under a frozen
clock; the session controller against a fake room under fake timers. Not automatable, so checked
by eye: pixel-closeness to the reference ("smooth" is checked by the CPU figure instead). An
Electron launch test belongs to sub-project D.
