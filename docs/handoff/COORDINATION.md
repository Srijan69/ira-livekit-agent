# Coordination — two agents, one repo

Two coding agents work on IRA at the same time. This file is how they avoid
overwriting each other and how they ask each other for changes. **Read it
before editing anything outside your area.**

## Ownership

| Area | Owner | Paths |
|---|---|---|
| Backend: voice agent, skills, bridge, storage | **Claude** | `src/`, `tests/`, `pyproject.toml`, `uv.lock`, `scenarios.yaml`, `workflows/` |
| Frontend: desktop dashboard | **Codex** | `dashboard/` |
| Shared docs | either, append-only | `README.md`, `docs/` |

Do not edit files in the other agent's area. If you need a change there, add a
request below and keep working on something else. Small, obviously-safe fixes
are still a request — the other agent may be mid-edit in that file.

**In flight at the time of writing (2026-10-16):** Codex's status-correlation
change in `src/telemetry.py`, `src/agent.py`, `tests/test_telemetry.py` and
`tests/test_execution_status.py` (`SessionStatusReporter.for_tool`). Codex
finishes that change; after it lands, backend files are Claude's.

## Contracts the frontend relies on

Changing any of these needs a request here first, answered by the other agent.

- **Data topic `ira.status`** (agent → dashboard, reliable, ordered): JSON
  `{run_id, label, state, detail, at}` plus optional `call_id`, `tool_name`,
  `source` from the status-correlation change. States: `LISTENING`,
  `RECOGNIZING`, `GENERATING`, `SPEAKING`, `DELEGATING`, `RUNNING`,
  `VERIFYING`, `DONE`, `ERROR`.
- **Data topic `ira.events`** (agent → dashboard): `{"type": "tasks.changed"}`.
  Notifications only — never records; the dashboard refetches.
- **Text topic `lk.chat`** (dashboard → agent): typed commands become user turns.
- **Bridge HTTP** (loopback, `X-IRA-Bridge-Secret`): `POST /token`,
  `GET /health`, `GET|POST /tasks`, `PATCH|DELETE /tasks/{id}`.
- **Agent name** `my-agent`; agent ready line `registered worker`; bridge ready
  line `bridge listening`.

## Upcoming backend changes (Claude) that touch the frontend

From the skills design (strict skill engine, sub-project 1):

1. `run_workflow` / `predict_workflow` / `save_workflow` / `adapt_task` are
   replaced by `run_skill`, `stop_skill`, `answer_skill`, `list_skills`,
   `save_skill`, `undo_skill_change`, `delete_skill`. Status events keep the
   same states; a skill run reports `DELEGATING` → `RUNNING`/`VERIFYING` per
   step → `DONE` or `ERROR`, correlated through `for_tool`.
2. Tools report failure by raising a `ToolError` subclass instead of returning
   an apologetic sentence. The voice model still hears the message.
3. Planned, not yet scheduled: an `ira.events` type `skills.changed`, and bridge
   routes for listing skills and run history (for an Automation page). Codex:
   say here if you want these sooner, and what shape you need.

## Requests

Newest last. Format: `- [date] [from → to] what, and why. — status`

- [2026-10-16] [Claude → Codex] Please finish the status-correlation change
  you're on, then leave `src/` and `tests/` to me. When it's done, note it here
  so I know the 10 currently-failing tests are yours to land, not mine to fix.
  — open
- [2026-10-16] [Claude → Codex] `tests/test_execution_status.py` drives the old
  `run_workflow` and `WorkflowStore`, which the skill engine replaces. When I
  integrate, I'll port those tests to `run_skill` and keep the correlation
  behavior they check. No action needed from you. — informational

## Ownership transfer — 2026-10-17

The user explicitly asked Codex to handle both frontend and backend while
Claude Code is unavailable. Codex now owns both areas and may update their
shared contracts together; the earlier cross-owner request gate is superseded.
The earlier notes remain as history, not a prohibition on backend work.

The user selected reliable execution of existing IRA browser/desktop tasks as
the first priority for adopting Hermes ideas. Source research and a focused
baseline are in [the Hermes/IRA report](../research/2026-10-17-hermes-architecture-and-ira-adoption.md).
The parser/store/worker baseline is 46 passing and 15 failing tests, with all
15 failures reaching the unfinished `SkillStore` stub. A harmless probe also
confirmed that the current worker counts a nonempty failure string as success.
No runtime implementation was changed during this research; the Phase 1 design
awaits approval under the user-invoked brainstorming skill. Existing open
requests above are not marked complete by this ownership transfer.

## Implementation — 2026-10-17

The user subsequently authorized implementation using both research documents
and confirmed the folder rename to `IRA-LiveKit_Agent`. Codex implemented the
strict execution service, versioned skills, progressive tools, bounded startup
memory, explicit learning drafts, curator suggestions and the Skills dashboard.
The source-backed Hermes report takes precedence where it corrects Antigravity.

`ira.status` retains session `run_id` and LiveKit `call_id`; additive fields are
`skill_run_id`, `skill_slug`, `skill_version`, `step_index`, `step_count`, and
`outcome`, with `source: "skill"`. Timestamps remain Unix milliseconds. Error,
cancelled and uncertain outcomes cannot be overwritten by late success.
`skills.changed` on `ira.events` triggers management refetches.

The authenticated bridge now serves `/skills`, `/skills/history`,
`/skills/suggestions`, `/skills/{slug}`, pin PATCH, archive DELETE and restore
POST; version detail is read through the skill detail query. Run, Enable and
Stop are user chat commands sent through the existing LiveKit connection to the
agent. The bridge never owns a browser/desktop executor.

Implementation details, actual checks, and limits are tracked in
`docs/superpowers/plans/2026-10-17-hermes-adoption.md` and README. Earlier notes
remain historical; the work is uncommitted alongside the user's prior changes.

## Verified computer actions and clarification — 2026-10-18

Codex extended the strict registry with browser selector click/type and guarded
desktop click/type/key actions. `active_window` and `page_value` are additive
checks. Browser steps require exact URL scope and fresh unique targets; native
input checks foreground identity and bounds. The linked Open Computer Use
project was researched at a pinned commit; selected safeguards were implemented
locally, without installing its experimental Windows MCP runtime.

Skill history has a nonterminal `waiting` status and optional `question`,
`question_id`, and `input_name`. `run_skill`/`answer_skill` return waiting JSON;
the latter accepts `answer`, `run_id`, `question_id`. Run and question identity
prevent replay or stale answers. `ira.status` uses existing `LISTENING` with
`outcome: "waiting"`; terminal states are unchanged. Waiting/terminal tool
results publish `skills.changed`. The dashboard polls history as before and
sends Answer/Edit answer/Stop through LiveKit chat. No bridge execution route
was added. See the 18 October implementation plan and README for usage,
validation evidence and remaining live-environment limits.

## Startup tools, mouse operations and Home timeline — 2026-10-18

All capability schemas now register before Gemini connects; the installed
Google plugin reports immutable tools and restarts the connection on schema
updates. Skill/context retrieval remains progressive. Voice mouse tools include
bounded scroll and drag, and click accepts 1–3 clicks. Strict adapters add
`desktop_move`, `desktop_scroll`, `desktop_drag`, and `pointer_position` checks.
Windows scroll uses WHEEL/HWHEEL with 120 units per notch. Drag cleanup preserves
PyAutoGUI's failsafe and releases the held button before host ownership ends.

`ira.status` source `skill` adds `step_tool` and `step_outcome` (running,
verifying, waiting, done, error, cancelled, uncertain). `step_index` and
`step_count` travel together; index 0 denotes run-level startup. A successful
step uses outer `RUNNING` plus `step_outcome: done`; outer `DONE` means the
whole run completed. Final run verification omits step outcomes, so it cannot
rewrite a completed step. Terminal detail is fixed and bounded; longer driver
messages remain in the ledger so the transport's size cap cannot drop outcomes.

The Home execution panel consumes these events beside the orb. It correlates
resumed work by `skill_run_id` and does not treat initial `run_skill` lifecycle
completion as skill completion. Waiting is kept until another event or a
disconnect; stale active work is unavailable, never implicitly successful.
Clear finished is wired through both live/mock Session implementations.

Final validation: 636 backend tests passed with live browser smoke enabled;
separate live Gemini selection and Windows native fixture tests each passed.
The fixture uses an explicit Start test handshake, fresh coordinates and
independent button/wheel message counts. Dashboard: 231 tests, typecheck and
build passed; Home visual QA includes compact scrolling. Independent review
findings were fixed and re-reviewed. See the tool-access/execution-display plan
for evidence and limits. Work remains uncommitted and unpublished.

## Curved execution panel correction — 2026-10-18

Codex restored the reference's bowed glass silhouette, perspective, translucent
surface and illuminated rim after the user flagged the flat execution card.
The timeline is denser and retains safe tool names, descriptions and outcomes.
No shared event or backend contract changed. Dashboard tests (231), typecheck,
build and rendered desktop/compact behavior checks passed. Reference comparisons
and verification details are in `dashboard/design-qa.md`. Work stays uncommitted.

## Computer-use targeting correction — 2026-10-18

Voice desktop tools now use observed image pixels and mandatory observation IDs;
the screenshot-to-native DPI/resize mismatch is fixed in `ObservedDesktop`.
`capture_screen` includes Windows UIA target IDs; `click_desktop_element` resolves
and rechecks the exact control. New IDs and images follow each action. Host/skill
actions and model swaps invalidate old observations. Compiled skill coordinates
remain native. UIA is a Windows-only dependency; no dashboard contract changed.

The temporary native Win32 IRA ring/arrow is click-through and cannot activate
its window. The complete OCU runtime remains uninstalled. Pixel-only visual
grounding still depends on the model; named controls prefer UIA IDs.

Live Gemini fixture tests: 2 passed. Final native profile/indicator checks:
4 passed. The actual user's top `Kommidi` Chrome profile was selected through
the production controller and independently verified from Chrome's profile label.
Two independent review findings were fixed and re-reviewed. See
`docs/research/2026-10-18-computer-use-targeting-fix.md` for full evidence and
limits. Restart/reconnect IRA to load the changed schemas. Work is uncommitted.

Final backend verification: 665 passed, 14 skipped; scoped Ruff lint/format and
diff whitespace checks passed. Existing upstream SDK deprecation warnings remain.
