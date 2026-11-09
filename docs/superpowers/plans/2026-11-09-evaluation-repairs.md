# IRA evaluation repair implementation plan

**Goal:** publish three verified backend repairs independently of the evaluated checkout's other uncommitted work.

**Base:** `main` at `f5621cbceca8e4b19c2169fe045b3204ccf251f8`.

**Spec:** [evaluation repairs](../specs/2026-11-09-evaluation-repairs.md).

## Implemented

- [x] Write regression tests for default scope denial, uncertain launch outcomes, and trusted local deadlines. Reproduce the defects before implementation.
- [x] Restore the existing semantic judge and restricted fallback; preserve bounded checks and cancellation.
- [x] Add pure exact app selection, a fixed hidden Windows catalogue command, and a permit-checked native launch. Replace the legacy Start-menu implementation and report pending verification.
- [x] Resolve supported relative deadlines inside `add_task`, using its bound human request and injected clock. Return the actual saved UTC and local time.
- [x] Adapt existing launcher tests to simulate both catalogue lookup and native launch; retain cancellation, telemetry, and persistence checks.
- [x] Transplant only these repairs onto clean `main`. Exclude unrelated browser, memory, dashboard, and continuation edits from the evaluated checkout.

## Verification and publication

- [x] Confirm the repair regression suite fails on unchanged `main`: eight failures and two passes before implementation.
- [x] Run the 16 selected offline suites on this clean branch, plus Ruff and diff checks; record results in [the repair report](../../research/2026-11-09-evaluation-repairs.md).
- [x] Prepare `fix/ira-evaluation-repairs` and its validation summary for a pull request against `main`.
- [ ] After integration, run three microphone-muted typed attempts each for deadlines and missing-app behavior, then scope and held workflows in a contained Windows environment.

No app restart, live evaluation, or desktop input is part of publication. Original live verdicts remain unchanged; automated tests are not live capability retests.

The final clean-branch run passed 433 tests with four opt-in live-provider tests skipped. Repository-wide Ruff lint and formatting checks passed. Push the prepared branch and create a pull request; the pull request records publication and review status.
