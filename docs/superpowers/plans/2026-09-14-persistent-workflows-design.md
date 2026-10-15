# Persistent Workflows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make IRA faster to use, preserve durable conversation knowledge, and let her save and reuse repeatable tasks as editable Markdown workflows.

**Architecture:** Keep SQLite for searchable facts and add a focused Markdown workflow store under `workflows/`. The agent receives only the relevant workflow text when asked to perform or teach a task, while explicit tools create, list, inspect, and update workflows. Existing browser and memory calls remain asynchronous and error-safe.

**Tech Stack:** Python 3.10+, LiveKit Agents, SQLite, Markdown files, pytest, uv, Ruff.

**Spec:** Approved design in the preceding conversation.

## Global Constraints

- Keep `src/agent.py` as the LiveKit entrypoint.
- Use `uv` for tests and tooling.
- Keep blocking SQLite and browser work off the realtime event loop.
- Do not add a separate STT or TTS stage.
- Workflow files must remain human-editable Markdown.

---

### Task 1: Markdown Workflow Store

**Files:**
- Create: `src/workflows.py`
- Test: `tests/test_workflows.py`

**Interfaces:**
- `WorkflowStore(root: Path | str | None = None)` stores files under `IRA_WORKFLOWS_DIR` or `<project>/workflows`.
- `save(name, goal, steps, notes="") -> str` creates or replaces a slugged Markdown file.
- `list() -> list[Workflow]`, `get(name) -> Workflow | None`, and `delete(name) -> str` provide durable CRUD.
- `WorkflowStore.render(workflow) -> str` returns bounded task instructions for the model.

- [ ] Write tests for save/reopen, slug normalization, list/get, replacement, delete, and bounded rendering.
- [ ] Run `uv run pytest tests/test_workflows.py -q` and verify failure because the module does not exist.
- [ ] Implement the Markdown format and parser with no external dependency.
- [ ] Run the focused tests and verify they pass.
- [ ] Run Ruff on the new module and tests.

### Task 2: Agent Workflow Tools

**Files:**
- Modify: `src/agent.py`
- Modify: `tests/test_agent.py`

**Interfaces:**
- Add `save_workflow`, `list_workflows`, `run_workflow`, and `delete_workflow` function tools.
- Add `WorkflowStore` injection to `Assistant` so tests use a temporary directory.
- `run_workflow` returns the stored goal and ordered steps; it does not silently execute arbitrary destructive actions.

- [ ] Write failing registration and persistence-through-tool tests.
- [ ] Run the focused tests and verify the expected missing-tool failures.
- [ ] Implement asynchronous wrappers around the synchronous Markdown store.
- [ ] Add concise instructions that teach IRA when to save and reuse workflows.
- [ ] Run the focused tests and verify they pass.

### Task 3: Durable Task and Conversation Notes

**Files:**
- Modify: `src/memory.py`
- Modify: `src/agent.py`
- Modify: `tests/test_browser_and_memory.py`
- Modify: `tests/test_agent.py`

**Interfaces:**
- Add `MemoryStore.remember(..., kind="event")` coverage for task notes and a `recent(limit=...)` reader.
- Add an agent `save_note` tool for explicit durable notes about a conversation, unfinished task, or preference.
- Include a bounded recent-note section in startup instructions, separate from the existing high-value seed facts.

- [ ] Write failing tests for note persistence across reopen and startup inclusion.
- [ ] Run those tests and verify failure.
- [ ] Implement the bounded recent-note query and tool.
- [ ] Run the focused tests and verify they pass.

### Task 4: Latency and Fast Task Routing

**Files:**
- Modify: `src/agent.py`
- Modify: `tests/test_agent.py`
- Modify: `README.md`

**Interfaces:**
- Tune the end-of-turn silence setting to reduce dead air while retaining high end sensitivity.
- Keep all blocking workflow, memory, desktop, and browser operations behind `asyncio.to_thread`.
- Update instructions to prefer direct workflow and Spotify tools over multi-step generic browsing when a matching task exists.

- [ ] Write a failing assertion for the new latency target and direct workflow guidance.
- [ ] Run it and verify failure.
- [ ] Implement the smallest configuration and prompt changes.
- [ ] Run the complete `uv run pytest -q` and `uv run ruff check src tests`.
- [ ] Update README setup and voice examples.
