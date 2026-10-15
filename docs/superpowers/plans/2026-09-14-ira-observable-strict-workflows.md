# IRA Observable Strict Workflows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add terminal lifecycle visibility, strict detailed Markdown workflows, screen and mouse control, and size-based worker delegation to IRA.

**Architecture:** Keep the existing `Assistant` as the conversation owner. Add focused modules for telemetry, desktop control, workflow execution, and worker routing; all blocking operations run through `asyncio.to_thread`. The router sends simple linear workflows to a deterministic local worker and complex or visual workflows to a powerful worker behind the same structured result interface.

**Tech Stack:** Python 3.10+, LiveKit Agents, Gemini realtime, PyAutoGUI, Selenium, Markdown, pytest, uv, Ruff.

**Spec:** `docs/superpowers/specs/2026-09-14-ira-observable-strict-workflows-design.md`

## Global Constraints

- Keep `src/agent.py` as the LiveKit entrypoint.
- Use `uv` for tests and tooling.
- Keep blocking SQLite, Selenium, PyAutoGUI, and file operations off the realtime event loop.
- Do not add a separate STT or TTS stage.
- Existing simple workflow Markdown files must remain readable.
- Workers may execute only allowlisted workflow actions.
- Telemetry failures must never interrupt a voice session.

---

### Task 1: Terminal Telemetry

**Files:**
- Create: `src/telemetry.py`
- Modify: `src/agent.py`
- Test: `tests/test_telemetry.py`
- Test: `tests/test_agent.py`

**Interfaces:**
- `SessionStatusReporter(run_id: str | None = None, sink: Callable[[str], None] | None = None)`.
- `reporter.emit(state: str, detail: str = "") -> None` prints a timestamped, elapsed-time line and never raises.
- `reporter.child_run(label: str) -> SessionStatusReporter` creates a correlated worker reporter.

- [ ] Write tests asserting stable state formatting, elapsed time, run-id correlation, and swallowed sink failures.
- [ ] Run `uv run pytest tests/test_telemetry.py -q` and verify the new tests fail because the module is absent.
- [ ] Implement the reporter with a default `print` sink and a monotonic clock.
- [ ] Add session and worker boundary emissions in `src/agent.py` without placing telemetry text in model instructions.
- [ ] Run the focused tests and `uv run ruff check src/telemetry.py src/agent.py tests/test_telemetry.py tests/test_agent.py`.

### Task 2: Desktop Screen and Mouse Controller

**Files:**
- Create: `src/desktop.py`
- Modify: `pyproject.toml`
- Test: `tests/test_desktop.py`

**Interfaces:**
- `DesktopController(screenshot_fn=None, mouse=None, screen_size_fn=None)`.
- `capture_screen() -> bytes` returns PNG bytes from the current screen.
- `click(x: int, y: int)`, `move(x: int, y: int)`, `scroll(clicks: int)`, and `type_text(text: str)` validate inputs and call the injected backend.
- `screen_size() -> tuple[int, int]` reports the active display size.

- [ ] Write tests for coordinate bounds, control-character sanitization, screenshot bytes, and backend errors.
- [ ] Run `uv run pytest tests/test_desktop.py -q` and verify failure before implementation.
- [ ] Implement lazy PyAutoGUI loading and bounded input validation.
- [ ] Add `mss` only if the existing PyAutoGUI screenshot path cannot provide PNG bytes; otherwise keep dependencies unchanged.
- [ ] Run the focused tests and Ruff.

### Task 3: Detailed Strict Workflow Model

**Files:**
- Modify: `src/workflows.py`
- Modify: `workflows/log-error.md`
- Modify: `workflows/play-my-favorite-spotify-funk-songs.md`
- Test: `tests/test_workflows.py`

**Interfaces:**
- `WorkflowStep(number: int, action: str, arguments: dict[str, object], expected: str, verify: str = "")`.
- `Workflow` gains `worker`, `complexity`, `prerequisites`, `allowed_tools`, `failure_policy`, and `history` fields with backward-compatible defaults.
- `WorkflowStore.save_detailed(...) -> str` writes the expanded Markdown format.
- `WorkflowStore.classify(workflow) -> str` returns `"lightweight"` or `"powerful"` using the approved five-step and visual/branching rules.
- `WorkflowStore.render(workflow) -> str` includes only bounded task instructions and the declared allowlist.

- [ ] Write failing tests for legacy-file loading, detailed round trips, malformed-step rejection, classification, and bounded rendering.
- [ ] Run the focused tests and confirm failure before implementation.
- [ ] Implement parsing and rendering with the standard library only.
- [ ] Normalize the two existing workflow files to the detailed format while preserving their intent.
- [ ] Run `uv run pytest tests/test_workflows.py -q` and Ruff.

### Task 4: Worker Execution and Routing

**Files:**
- Create: `src/workers.py`
- Modify: `src/agent.py`
- Test: `tests/test_workers.py`
- Test: `tests/test_agent.py`

**Interfaces:**
- `ExecutionResult(status: str, workflow: str, completed_steps: int, total_steps: int, failed_step: int | None = None, message: str = "")`.
- `ToolRegistry` maps allowlisted action names to async callables.
- `LightweightWorker.run(workflow, registry, reporter) -> ExecutionResult` executes steps in order and halts on the first failure.
- `PowerfulWorker.run(workflow, registry, screen, reporter) -> ExecutionResult` receives the same allowlist and can request fresh screen state for visual steps.
- `WorkflowRouter.run(workflow, registry, screen, reporter) -> ExecutionResult` selects the worker from `WorkflowStore.classify`.

- [ ] Write failing tests for lightweight routing, powerful routing, allowlist rejection, step halting, and telemetry emission.
- [ ] Run `uv run pytest tests/test_workers.py -q` and verify failure.
- [ ] Implement the structured result and deterministic worker first.
- [ ] Implement the powerful worker as an injected strategy with a conservative default that refuses undeclared actions.
- [ ] Run focused worker and agent tests.

### Task 5: Agent Integration and LiveKit Lifecycle Hooks

**Files:**
- Modify: `src/agent.py`
- Modify: `tests/test_agent.py`
- Modify: `README.md`

**Interfaces:**
- Add an `execute_workflow` function tool that loads a named workflow and delegates it through `WorkflowRouter`.
- Route existing desktop/browser tool calls through the worker registry so direct calls also produce `DELEGATING`, `RUNNING`, and `DONE` states.
- Attach verified `AgentSession` lifecycle callbacks for listening, recognition, generation, and function-call boundaries; use a safe fallback reporter when an SDK event is unavailable.

- [ ] Write failing tests for tool registration, workflow delegation, lifecycle state mapping, and failure wording.
- [ ] Run focused tests and confirm failure.
- [ ] Implement integration using the installed LiveKit APIs verified against current documentation/source.
- [ ] Keep telemetry output concise and exclude screenshots, secrets, and raw model prompts.
- [ ] Run `uv run pytest tests/test_agent.py tests/test_workflows.py tests/test_workers.py tests/test_desktop.py tests/test_telemetry.py -q`.
- [ ] Run `uv run ruff check src tests` and `uv run ruff format --check src tests`.

### Task 6: Documentation and Full Verification

**Files:**
- Modify: `README.md`
- Modify: `ROADMAP.md`

- [ ] Document terminal states, workflow metadata, size classification, screen permissions, and the local-machine requirement.
- [ ] Document how to inspect the workflow folder and execution history.
- [ ] Run the complete `uv run pytest -q` and record provider-dependent skips or failures accurately.
- [ ] Run Ruff checks and inspect the final diff for unrelated changes.
