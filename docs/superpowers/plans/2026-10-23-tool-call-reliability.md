# IRA Tool Call Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Track completed steps here and keep task reports beside the independent audit evidence.

**Goal:** Fix the verified voice-authority, quota-policy, argument-validation and unbounded-execution failures while retaining working authorized commands.

**Architecture:** Keep LiveKit 1.8.8, upfront tool registration, request admission, and cooperative host ownership. Bind only matching finalized human input or an explicit result continuation; repair provider arguments before typed validation; bound host waiting/reporting without releasing ownership of a still-running thread.

**Tech Stack:** Python 3.14, LiveKit Agents/Google 1.8.8, google-genai 2.31, pytest, existing Electron/React dashboard.

**Spec:** C:/Users/kommi/VS code projects/livekit-voice-agent/audit/2026-10-23/IRA_ANTIGRAVITY_REVIEW.md and its independent test file. User explicitly requested implementation after the review. The downloaded fix brief is reference guidance, not authorization to weaken safety or replay unknown effects.

## Global Constraints

- Preserve pre-existing uncommitted changes; work on the current feature checkout because it contains the implementation being repaired.
- Do not create commits containing the large pre-existing untracked implementation. Keep before-file copies and scoped diffs for review.
- No real desktop effects, outgoing messages, secrets in logs, or blanket automatic replay of uncertain effects in tests.
- Do not edit installed packages or dynamically mutate the provider tool list.
- Use `uv run --frozen --no-sync python -m pytest` (the pytest executable shim is broken).
- Provider-facing canonical schemas, tool flags and duplicate handling must remain equivalent. Existing direct Python calls must remain usable.
- Existing valid voice, text, skill clarification, cancellation and continuation behavior must have positive tests; rejecting all work is not a fix.

## Task 1: Human binding and policy failures

Files: `src/request_scope.py`, `src/request_scope_livekit.py`, `tests/test_request_scope.py`, `tests/test_request_scope_livekit.py`, `tests/test_tool_execution_resilience.py`, new `tests/test_request_scope_boundaries.py`.

Consumes: actual SDK generation/transcription order; `repair_function_tool(tool)` from Task 2 at wrapping return boundary. Produces: correctly bound wrapped tools, structured scope failures and correlated admission logs.

- [ ] Run copied independent no-transcript, finished-request, changed-input and quota-expansion tests and record the failing cases.
- [ ] Replace model-inferred requests and latest-request fallbacks with exact source binding. Add bounded waiting only for the same unresolved generation; stop/new input/session close must invalidate the waiter. Example acceptance: start tool, observe no effect, deliver matching final transcript, await exactly one simulated effect. Unrelated final input must not authorize it.
- [ ] Remove broad quota permissions. Fail closed for effects whose relation cannot be established; keep narrow, deterministic safe actions if exact intent is proven. Test outage with inspection-only requests and legitimate explicit commands. Bound retries within overall policy deadline and avoid mandatory confirmation language for transient failures.
- [ ] Add structured `ToolError` messages for guard failures with status, retryability and concise model instructions; preserve genuine target clarification. Keep permit cleanup on every exit and log IDs without raw arguments.
- [ ] Replace existing tests that explicitly bless unsafe inference/resumption with boundary assertions and legitimate finalized-input/continuation success tests.
- [ ] Run scope/integration/clarification/failover and independent audit tests. Record actual failures and fixes in the task report.

## Task 2: Provider argument validation

Files: `src/tool_dispatch.py`, new `tests/test_tool_dispatch_boundary.py`; scope integration is owned by Task 1.

Interface: `repair_function_tool(tool)` returns a provider tool that repairs supported aliases before validating the original signature. RawFunctionTool with unchanged original JSON schema is one supported SDK mechanism. The returned object must remain usable for direct calls through the original Python signature, where practical; any metadata change is documented and tests should verify external schema rather than old private type identity.

- [ ] Reproduce `prepare_function_arguments(fnc=wrapped, json_arguments='{"name":"Notepad"}', call_ctx=real_context)` rejecting an alias before wrapper execution.
- [ ] Implement alias repair at the supported SDK boundary, then validate canonical types/required fields. Preserve canonical schema, flags, duplicate policy, context injection, defaults, positional/keyword callers, and existing raw tools. Conflicting alias/canonical values must be rejected instead of silently choosing an action.
- [ ] Test actual SDK preparation plus invocation for canonical, alias, malformed/missing/type-invalid/conflicting input, raw tools, and schema equality. No monkeypatch to SDK functions or site-packages.
- [ ] Notify Task 1 of the interface, run focused tests and write TDD/results report.

## Task 3: Host execution deadlines

Files: `src/skill_tools.py`, `src/host_execution.py` if needed, new `tests/test_tool_timeouts.py`; callers may be changed only in coordination with the controller.

Produces: bounded `_run_host_action` queue and result wait, with host ownership retained while actual worker thread is unsettled. Use explicit constructor/configurable attributes or module defaults for short deterministic tests; normal direct actions should use a practical 30-second overall budget. Distinguish not-started queue timeout from an uncertain running effect.

- [ ] Demonstrate a contended lock and blocked worker keeping the caller pending; use events and inert functions, never native input.
- [ ] Bound acquisition plus result wait with one deadline. Raise a meaningful ToolError-compatible error on timeout. Cancellation/timeout must not free a lock held by an executing native worker or permit new overlapping actions. Retain and drain worker tasks, and do not claim completion on late success.
- [ ] Test queue timeout/no actuation, running timeout, cancellation, eventual ownership release, subsequent valid action, and ordinary success/error paths.
- [ ] Run host/skill/execution tests, document settings and any needed caller integration; write task report.

## Task 4: Integrated verification and review

- [ ] Compare exact before-file snapshots with the resulting files; independently review each task for spec and quality.
- [ ] Run all offline Python tests after checking opt-in host/network suites, plus dashboard tests/typecheck if a shared status contract changes.
- [ ] Run the original independent audit tests against the fixed checkout; all original unsafe boundary cases must pass, alongside positive tests.
- [ ] Verify structured failures reach the SDK terminal/UI lifecycle and that policy/host deadlines do not leave requests indefinitely pending. Add bounded simulated provider/host tests for gaps.
- [ ] Update the review with implemented fixes, exact commands/results, and honest live-voice limits. Complete the goal only after all required fixes and checks are done.
