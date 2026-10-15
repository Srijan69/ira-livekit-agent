# IRA Observable Strict Workflows Design

## Goal

Make IRA visibly observable during a voice session, execute saved Markdown workflows strictly through delegated workers, and give workers safe screen and mouse capabilities for desktop tasks.

## Scope

The change has four cooperating parts:

1. A terminal status reporter for listening, recognition, generation, delegation, tool execution, verification, completion, and failure.
2. A richer Markdown workflow format with explicit execution metadata and strict step handling.
3. A desktop controller that captures screenshots and performs mouse and keyboard actions off the realtime event loop.
4. A worker router that sends small linear workflows to a lightweight local worker and complex or visual workflows to a powerful worker.

## Behavior

Small workflows have at most five linear steps, no branching or loops, no visual interpretation, and only deterministic actions already exposed by the agent. All other workflows use the powerful worker. A worker may execute only declared workflow actions. When a step cannot be verified, execution stops at that step and returns a failure with the step number and reason.

The main voice agent remains responsible for conversation and concise spoken confirmations. Browser, desktop, memory, workflow, and worker operations remain off the realtime event loop. Terminal output is diagnostic only and is never inserted into model context.

## Workflow Markdown

Each file has YAML-like front matter with `name`, `goal`, `worker`, `complexity`, `version`, `prerequisites`, `allowed_tools`, `failure_policy`, and `last_run`. The body contains sections for `Goal`, `Prerequisites`, `Steps`, `Verification`, `Recovery`, and `Execution history`. Existing simple workflow files remain readable and are normalized with safe defaults when loaded.

Each step has a stable number, an action name, human-readable arguments, an expected result, and an optional verification rule. The parser rejects malformed or empty steps rather than silently producing an executable workflow.

## Telemetry

Status lines include an ISO timestamp, run id, state, and elapsed milliseconds. State transitions are emitted from the session lifecycle and worker boundaries. At minimum, the terminal shows `LISTENING`, `RECOGNIZING`, `GENERATING`, `DELEGATING`, `RUNNING`, `VERIFYING`, `DONE`, and `ERROR`.

## Desktop vision and input

Screen capture and mouse or keyboard actions use PyAutoGUI behind a small controller interface. Screenshot capture returns a bounded PNG artifact for a worker and never blocks the asyncio event loop. Mouse movement, click, scroll, and typing are explicit capabilities, with coordinates validated against the current screen size. The powerful worker may request a fresh screenshot before a visual step and must verify the resulting screen state before continuing.

## Worker interfaces

`WorkflowRouter` selects a worker from parsed workflow metadata. `LightweightWorker` executes deterministic declared actions through an injected tool registry. `PowerfulWorker` receives the workflow, current screen context when requested, and the same allowlisted tool registry; it may choose among declared steps but may not invent tools. Both workers emit telemetry and return a structured execution result that the voice agent summarizes.

## Error handling

Worker failures are caught and converted into structured results. The voice agent reports the failed step and does not claim completion. Browser or desktop unavailability is a capability failure, distinct from a page or application failure. Telemetry logging must never raise into the LiveKit session.

## Testing

Tests cover telemetry formatting and state transitions, screen and input validation, backward-compatible workflow parsing, strict action allowlists, small-versus-powerful routing, failure halting, and agent tool registration. Live provider tests remain optional and are skipped when credentials are unavailable.
