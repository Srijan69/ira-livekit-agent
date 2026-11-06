# Live Widgets and Gemini Failover Implementation Plan

> **For agentic workers:** Use test-driven development and scoped parallel agents. Follow the agreed handoff, then review the integrated diff and run the offline suites.

**Goal:** Complete sub-project C and migrate IRA to Gemini 3.8 Live with automatic native-audio fallback.

**Architecture:** Preserve Electron as a thin client, Python SQLite as the task source, the authenticated loopback bridge for records, and LiveKit reliable events for invalidation. Keep direct speech-to-speech; use the supported LiveKit realtime fallback mechanism if available.

**Tech Stack:** Python/uv/SQLite/aiohttp/LiveKit, Electron/React/TypeScript/Vitest, Open-Meteo.

**Spec:** `docs/handoff/HANDOFF.md`, section 7.1, and the user's model migration request.

## Constraints and decisions

- Keep `src/agent.py` as entrypoint. No separate STT or TTS.
- Tests use temporary stores, injected clocks/backends/transports, and fake rooms; no real desktop, microphone, personal database, or provider requests.
- Start with C1. Preserve the current branch and the completed Home layout.
- User answered C4: a `current_time` tool. User answered C7: Hyderabad default with editable location. User answered C8: ask one focused question, then help.
- Primary: `gemini-3.8-live`; fallback: `gemini-2.5-flash-native-audio-latest`. Verify SDK support, test failover, document the older fallback's retirement exposure.
- All task timestamps use timezone-aware ISO-8601 UTC; renderer formats local dates.
- Bridge secret remains in Electron main; expose named IPC only.

## Work and verification

- [x] C1: `tests/test_memory_contention.py` demonstrates two writers sharing a temporary DB with explicit busy policy, then update `src/memory.py` and run memory tests.
- [x] C2–C3: `src/tasks.py`, `tests/test_tasks.py`, `src/bridge.py`, `tests/test_bridge.py`: lazy TaskStore, UTC validation, bounded titles, ordering/null dates, toggle/delete and authenticated CRUD with 400/404 responses. Run focused tests before and after implementation.
- [x] Model: `src/realtime_model.py`, `tests/test_realtime_model.py`, dependencies, and agent integration. Test primary success, failover, exhausted providers, context/settings preservation, and cleanup using fake providers. Update obsolete model/greeting assertions.
- [x] C4: inject TaskStore into Assistant; implement `current_time`, `add_task`, `list_tasks`, `complete_task`, concise instructions, reliable `ira.events` notifications, simulation mocks and one task scenario. Tests assert persisted changes and fake-room notifications.
- [x] C5: shared task types, authenticated main proxy, preload `tasks.*`, generic session event subscriptions, `useTasks`, and UpcomingTasks integration. Test fetch/retry, event refresh, optimistic update/rollback, and persistence through the real temporary bridge/store.
- [x] C6: `src/system_status.py` with injected psutil loader and primed CPU sampling; declare psutil; add a voice tool. Test fake metrics, no battery, and unavailable backend.
- [x] C7: verify Open-Meteo current parameters; build tested WMO mapping, location selection and weather hook. Cache per location, retain stale readings after failures, refresh every 15 minutes, pause hidden work. Test with fake fetch, clocks and storage.
- [x] C8: refine quick actions with user input; keep unsupported attachments disabled; replace mock user data with explicit local profile defaults; document dashboard run instructions and unfinished pages.
- [x] Review and verify: full offline pytest, Ruff lint/format, Vitest, both TypeScript configs, production build, and browser visual/interaction check with fake session. Record provider checks separately. Update handoff with accurate completion and remaining limitations.

## Completion evidence — 2026-10-15

- C1 red/green: the contention test failed with `database is locked` before adding the explicit busy timeout, then passed.
- Model red/green: incompatible Extended Thinking override is rejected; fake-provider tests cover timeout, synchronous failure, cancellation, exhaustion, context preservation, cleanup and no action replay.
- Review red/green: a pending checkbox save could shrink an expanded list back to eight rows; the regression test failed, then passed after using the current refresh callback.
- Final Python suite: `uv run pytest -q -p no:randomly` — **306 passed, 4 skipped**. The skipped tests require `IRA_RUN_LIVE_EVALS=1`; one upstream google-genai deprecation warning remains.
- Final Ruff: `uv run ruff check src tests` and `uv run ruff format --check src tests` — passed (26 files formatted).
- Final dashboard: `npm test` — **133 passed**; `npm run typecheck` and `npm run build` — passed.
- Browser QA: isolated headless session at 1440×900 with fake IPC, weather, metrics and a scripted voice session. Verified View All, completion persistence in the fixture, save rollback, city search/selection/persistence, stale reading retention, disabled attachments and the focused quick-action prompt. No page errors or horizontal overflow after correcting the test fixture. Closed browser and dev server afterwards.
- Preview saved to `docs/handoff/current-home-widgets.png`. README, ROADMAP, HANDOFF and `.env.example` now describe current behavior and limits.
- Current model/SDK and Open-Meteo docs were fetched directly from public documentation URLs. The installed LiveKit CLI 2.13.2 cannot run the newer docs/simulation commands; upgrade instructions are in the handoff. No CLI/MCP documentation lookup was used, so no docs feedback was submitted.
- Live voice, key-specific model access, actual provider failover and the cloud simulation were **not run**. No personal database, desktop tools or microphone were used by verification. Sub-project D and other sidebar pages remain future work.
