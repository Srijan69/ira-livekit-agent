# Hermes adoption implementation plan

> **For agentic workers:** Use the Superpowers development and verification workflows. Steps use checkboxes for tracking.

**Goal:** Make IRA's saved procedures execute truthfully, retain recoverable skill history, and expose verified outcomes and conservative learning in the existing voice/dashboard application.

**Architecture:** Gemini Live remains the controller. One agent-owned execution service validates a frozen skill version and dispatches typed actions under a shared lock, checking observations before reporting completion. The bridge reads persistent skills/history and edits metadata; dashboard execution travels through the existing LiveKit chat channel to the agent.

**Tech stack:** Python, LiveKit Agents, SQLite, Markdown, Electron, React, TypeScript.

**Specs:** `docs/research/2026-10-17-hermes-architecture-and-ira-adoption.md` (corrections and phase order take precedence); `docs/superpowers/specs/2026-10-16-self-improving-adaptive-skills-architecture.md` (Antigravity).

## Constraints and decisions

- The user authorized implementation on 17 October 2026, including both backend and frontend. Use the renamed IRA-LiveKit_Agent checkout and preserve all prior uncommitted work.
- Implement research phases 1–4 conservatively. Phase 5's open-ended model worker remains optional. No automatic model spending, automatic consolidation, or blind side-effect replay.
- Keep existing direct tools useful; retire the unsafe legacy execution path without deleting old workflow originals.
- No natural-language response parsing as proof of success. Plain strings at a strict execution boundary are unverified.
- Frozen startup context; one character budget across preferences/facts/recent notes. Keep provider sliding-window compression, accurately described.
- Preserve existing session `run_id` and call correlation. Add `skill_run_id`, `skill_slug`, `skill_version`, `step_index`, `step_count`, `outcome`; timestamps remain milliseconds.
- Desktop/browser execution remains owned by the voice agent process. The authenticated bridge must never create a second desktop executor.
- New learning starts as a draft; only verified runs supply positive demonstrations. Pin/archive/restore are recoverable. Curator suggests archival by default.

## Task 1: Skill files and persistent history

**Files:** `src/skill_format.py`, `src/skill_store.py`, `tests/test_skill_format.py`, `tests/test_skill_store.py`.

**Interfaces:** Extend `Skill` with `allowed_tools: tuple[str, ...] = ()`, `state: str = 'draft'`, `pinned: bool = False`, `source_run_id: str = ''`. Implement the existing SkillStore test contracts plus `find_by_phrase`, `catalog`, `restore`, `set_pinned`, and archived listing. Store immutable originals/version text, write atomically, and lock concurrent mutations. Existing hand edits remain observable and recoverable.

- [x] Run the existing store tests to capture the stub failures; add metadata round-trip, path containment, concurrency, archive/restore, and draft catalog tests.
- [x] Implement schema and store. Keep older skills readable and default them to draft until validated.
- [x] Run `uv run --no-sync python -m pytest tests/test_skill_format.py tests/test_skill_store.py -q` and scoped Ruff.

## Task 2: Authoritative execution and run history

**Files:** `src/skill_execution.py`, `src/skill_runs.py`, `src/workers.py`, execution tests.

**Interfaces:** `ToolOutcome(ok: bool, message: str = '', evidence: str = '')`; registry with explicit parameter validation; `SkillExecutionService.run(skill, registry, reporter, *, inputs=None, run_id=None)`; `stop(run_id)`; run store with `get(run_id)` and `list(limit=50)`. Checks are injected async observations returning bool/evidence. Service reports an immutable result with status, completed/total/failed step and run identity. Same delivery ID returns the existing result and cannot repeat actions.

- [x] Reproduce the nonempty-error-string false success, invalid later step side effects, failed checks, unknown inputs, cancellation, duplicate delivery, and deadline uncertainty in tests.
- [x] Implement preflight validation, explicit checks, bounded deadlines, a shared action lock, persisted terminal outcomes, and interruption recovery. Do not retry actions automatically.
- [x] Run execution/store tests and scoped Ruff. Preserve legacy imports but make the old worker fail closed on untyped outcomes.

## Task 3: Voice adapters, bounded memory, learning and curator

**Files:** `src/agent.py`, focused adapter modules, `src/memory.py`, `src/curator.py`, `src/skill_learning.py`, relevant tests.

**Interfaces:** Voice tools `run_skill`, `stop_skill`, `skill_status`, `list_skills`, `save_skill`, `undo_skill_change`, `delete_skill`, `learn_skill`. Existing browser/desktop services supply actual observations; tool failures raise LiveKit `ToolError`. `MemoryStore.startup_context(budget=4000)` returns deduplicated bounded text; preference corrections supersede explicit prior entries. `learn_from_run` requires a done verified source and saves a draft. Curator emits suggestions and honors pins, preserving versions.

- [x] Test adapter failure propagation, successful observed execution, cancellation, frozen prompt, bounded deduplicated memory, and refusal to learn from uncertain/error runs.
- [x] Implement thin adapters and registry; move long-running work off the event loop. Keep full details in persisted history and cap model observations.
- [x] Integrate explicit learning and conservative curator; optional background model compilation stays disabled without configuration.
- [x] Run focused tests, then the Python suite without external paid scenarios.

## Task 4: Authenticated management and correlated dashboard

**Files:** `src/bridge.py`, focused bridge skills module, `src/telemetry.py`, `dashboard/shared/types.ts`, Electron bridge/preload, session/execution reducer, Skills view and tests.

**Contract:** `GET /skills` returns `{skills: [{slug,name,description,version,source,state,pinned,archived,use_count,failure_count}]}`; `GET /skills/history` returns `{runs: [...]}`; `GET /skills/{slug}` returns skill metadata plus `markdown`; `PATCH /skills/{slug}` accepts `pinned`; archive/restore preserve history. All routes require the existing secret. UI Run/Stop invokes existing `sendText` to reach the agent. No HTTP execution route is advertised.

- [x] Add tests for auth, malformed mutations, terminal event ordering and run/step correlation, and UI loading/error/run/pin/stop behavior.
- [x] Implement read/edit APIs and IPC; replace the automation placeholder with a Skills view and add correlated progress/stop controls.
- [x] Run dashboard tests, typecheck, build, and browser visual verification if available.

## Task 5: Integration and review

- [x] Inspect all changes against both documents; run failure-focused end-to-end tests with local doubles for external hardware/provider boundaries.
- [x] Run Python suite, dashboard suite, typecheck/build and scoped lint. Fix integration failures rather than weakening behavioral assertions.
- [x] Update README and coordination notes with delivered contracts, rename, limitations, and commands for live Windows/Gemini smoke testing.
- [x] Record actual results here. Do not claim real playback or voice validation from mocked tests.

## Progress

- Planning: repository and both research documents inspected. Existing skill-store stub confirmed; older user changes preserved. Newer source-backed research resolves conflicts in the earlier spec.


## Delivered milestone and verification

Implemented the conservative scope above in the renamed checkout, preserving
prior uncommitted work. The runtime, versioned store, agent adapters, memory,
explicit learning drafts, curator suggestions and management UI are connected.
The built-in procedures cover app launch, page navigation and Spotify playback.

Final checks on 17 October 2026:

- Python: `uv run --no-sync python -m pytest -q --tb=short` — 483 passed,
  5 skipped. Four skips require an opt-in live provider; one requires Windows
  symlink privileges. One upstream Google SDK typing deprecation warning.
- Dashboard: `npm test` — 205 passed; `npm run typecheck` and `npm run build`
  completed successfully.
- Scoped Ruff checks passed. The Skills view was visually inspected using an
  isolated fixture; screenshot: `dashboard/shots/skills-2026-10-17.png`.
- Independent reviews caught and fixed queued cancellation, per-run identity,
  hand-made filename correlation, malformed later URLs, cross-connection memory
  deduplication, and live-owner recovery. Each behavior has regression coverage.

Execution is deliberately bounded: six typed registry operations, observable
window/page checks, no arbitrary shell actions, no prose-based visual judgments,
and no interactive ask/say inside a compiled run. Direct click/type/mouse tools
remain available via capability loading. Explicit learning produces a draft
from a parameterized successful source procedure; it does not yet synthesize a
novel procedure from arbitrary conversation or direct-tool traces. Automatic
model compilation, consolidation, model-weight training, and the optional
open-ended worker are not implemented. No live IRA model calls were made for validation.
Real Gemini audio, Windows app outcomes and signed-in Spotify playback have not
been smoke-tested in this implementation turn; no live success is claimed.

Retry verification: the full Python suite again passed 483 tests (5 skipped); dashboard again passed 205 tests, typecheck and build. A fresh scoped reviewer confirmed all five requested fixes were addressed with no remaining scoped blocker.
