# Verified computer actions and clarification implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development for the independent execution, adapter, and dashboard tasks. Preserve the existing uncommitted implementation.

**Goal:** Extend the strict skill engine to browser click/type and guarded desktop input, with clarification that resumes a run without replaying work.

**Architecture:** Keep Gemini Live, Electron, Selenium, and the agent-owned executor. Browser actions resolve a fresh unique CSS target and verify URL scope. Desktop actions check foreground-window identity and input bounds. A waiting coordinator releases the host lock, accepts an answer identified by run and question, revalidates inputs and observations, and continues the frozen skill version.

**Tech stack:** Python, asyncio, SQLite, Selenium, PyAutoGUI, LiveKit Agents, React/TypeScript.

**Spec:** The approved next milestone in this chat builds on `../specs/2026-10-16-self-improving-adaptive-skills-architecture.md` and `../../research/2026-10-17-hermes-architecture-and-ira-adoption.md`. The latter takes precedence. Review `../../research/2026-10-18-open-computer-use-adoption.md` for the linked upstream project's applicability.

## Constraints and decisions

- Actual checkout: `C:\Users\kommi\VS code projects\IRA-LiveKit_Agent`; the chat cwd is obsolete.
- Retain existing work on `feat/ira-desktop-dashboard`; do not reset, stash, commit, or publish unrelated changes.
- Every effect still needs an allowed tool, semantic argument validation, a typed outcome, and an observation. No autonomous planner or arbitrary code execution is added.
- Keep terminal outcomes immutable and in-flight effects owned until settled.
- `looks_like` and `say` remain unsupported. A window-title observation proves window state, not text entry or completed business work.
- Clarification answers are transient; redact them from execution records and tool diagnostics. User chat remains subject to its normal transport/history behavior.
- Use the linked MCP project as a source-backed design reference. Its experimental Windows runtime is not installed or started by this milestone.

## Tasks

- [x] **1. Upstream review:** inspect pinned source and license; document Windows support, observation/target rules, and what to adopt.
- [x] **2. Typed actions:** modify `src/skill_adapters.py`, `src/browser.py`, `src/desktop.py`. Add `browser_click(selector, expected_url)`, `browser_type(selector, text, expected_url, submit=False)`, `desktop_click(x, y, window_title, button='left')`, `desktop_type(text, window_title)`, and `desktop_press_key(key, window_title)`. Refuse ambiguous targets, URL drift, password/file fields, unsupported keys, and wrong focus. Tests start with failing behavioral examples in `tests/test_verified_actions.py`.
- [x] **3. Clarification engine:** modify `src/skill_execution.py` and `src/skill_runs.py`. `run()` returns `waiting` with `question`, `question_id`, `input_name`; `answer(run_id, answer, *, question_id)` continues. Test two questions, invalid/stale/duplicate answers, expiry, stop, lock release, resumed-state drift, source immutability, and no replay. Validate constant later steps before effects, then validate newly bound values before continuing.
- [x] **4. Voice and format integration:** modify `src/skill_tools.py`, `src/skill_format.py`, `src/agent.py`. Add `answer_skill`, expose waiting fields in `skill_status`, register `active_window` and `page_value` observations, preserve terminal-only outcome accounting, and notify dashboard at waiting/terminal transitions. Test actual Assistant-to-engine flow in `tests/test_skill_clarification_tools.py` and round-trip the new observations.
- [x] **5. Dashboard:** extend `SkillRun` additively and show a waiting question, answer input, and Stop. Commands keep using LiveKit chat; the bridge never executes. Test disconnected/empty-answer controls, matching question IDs, multiple runs, and refetch.
- [x] **6. Verification and documentation:** run focused tests, then the backend suite and dashboard tests/typecheck/build. Run a local browser fixture through the real Selenium controller and strict engine. Record actual live-environment coverage; do not claim Gemini audio or signed-in Spotify was tested without doing so. Review combined changes independently and address findings.

## Validation commands

```powershell
uv run --no-sync python -m pytest tests/test_verified_actions.py tests/test_skill_clarification_tools.py -q --tb=short
uv run --no-sync python -m pytest -q --tb=short
uv run --no-sync python -m ruff check src/agent.py src/skill_tools.py src/skill_format.py src/skill_execution.py src/skill_runs.py src/skill_adapters.py src/browser.py src/desktop.py tests/test_skill_clarification.py tests/test_skill_clarification_tools.py tests/test_verified_actions.py tests/test_skill_browser_live.py tests/test_skill_desktop_live.py tests/test_bundled_skills.py tests/test_skill_runs.py
```

From `dashboard/`, run `npm test`, `npm run typecheck`, and `npm run build` separately. Use temporary isolated skill/run stores for smoke tests.

## Progress and review record

The browser/desktop task and clarification task own separate production files. Parent owns voice/parser integration; dashboard changes consume only the additive history fields. Existing transport status states remain compatible; waiting is an outcome and durable run status.

Ruling: work in the existing renamed checkout because the prior milestone and its necessary source files are uncommitted here. A fresh worktree would omit the implementation being extended.

Independent review identified six issues, all reproduced, fixed, and rechecked:

1. An answer arriving while the waiting record was being published could resolve the wrong caller's future. Each waiting boundary now captures its own future before publication.
2. A rejected answer retains its question ID. The dashboard now offers Edit answer to retry without resetting duplicate protection on every refresh.
3. Constant `page_element` CSS values now pass pure preflight validation before earlier effects.
4. Browser typing resolves the selector again after `clear()` and requires the same unique target before sending text.
5. Voice status output trims prose fields before JSON serialization; correlation IDs and complete JSON remain intact, including Unicode/escaped questions.
6. Rendering forces quotes around `ask` questions, including single-word prompts.

The new `Fill Web Field` workflow is a draft with a preflight/round-trip test. It opens a reviewed URL, asks for text, fills a chosen field, and checks the exact value without submitting.

The Windows live fixture was attempted twice. Windows did not grant it foreground focus, including after an observation-based wait, so the test refused before click/type/key effects. This is an unverified live path, not a passing desktop smoke. The fixture process was closed. No personal application was typed into. Live Gemini audio and signed-in Spotify have not been exercised in this milestone.

Final validation on 18 October 2026:

- `IRA_RUN_BROWSER_SMOKE=1 uv run --no-sync python -m pytest -q --tb=short`: **612 passed, 6 skipped**. Skips are four opt-in provider evaluations, the separately attempted desktop fixture, and the Windows symlink privilege case. One upstream Google SDK typing deprecation warning remains.
- `npm test`: **221 passed** across 20 files.
- `npm run typecheck` and `npm run build`: passed.
- Scoped Ruff over the changed Python modules/tests: passed.
- Diff whitespace check: passed.
- Independent review: all six findings resolved and rechecked; no outstanding actionable findings in the milestone paths.
- Visual QA: local Vite mock session with synthetic skills/weather at 1440×960; Answer → Edit answer → resubmit → Stop passed. No horizontal overflow, Vite overlay, or runtime errors. Screenshot: `dashboard/shots/skills-waiting-2026-10-18.png` (static profile name redacted). The only console message was the existing missing favicon. Disposable browser and owned server were closed after capture; no production source was changed by QA.
