# IRA evaluation findings and backend repairs

Typed goal evaluation exposed three defects: a file request reached an unrelated playlist action, nonexistent application names opened web searches with false launch claims, and local task deadlines were saved 5½ hours late. This branch repairs those backend paths without including the evaluated checkout's other uncommitted work.

## Resulting behavior

| Trigger | Before | After |
|---|---|---|
| A file goal proposes an unrelated playlist action | Default request checks automatically allowed it. | Existing semantic checks run; restricted fallback rejects the unrelated action when the provider is unavailable. |
| Open an invented application name | Start-menu search could open Bing; feedback encouraged a launch claim. | Exact registered-app selection fails without a search fallback. Valid launch requests return uncertain status until independently observed. |
| Add a task tomorrow at 4:30 PM in UTC+05:30 | A model proposal of 16:30 UTC persisted as 10 PM local time. | The bound human request and injected local clock resolve 16:30 local to 11:00 UTC; feedback reports the saved deadline. |

The task schema and shared telemetry states are unchanged. The launch tool's DONE event means its call returned and includes verification-pending detail; the structured result and scope history remain uncertain. Ambiguous app names and supported relative deadlines require clarification.

## Validation

On clean unchanged `main`, the repair regression suite produced eight failures and two passes. This independently reproduces the scope, launch-feedback, and deadline defects before transplanting the repairs.

The final clean-branch run passed **433 tests with four opt-in live-provider tests skipped**, zero failures, and zero errors across 16 selected suites in 24.48 seconds. Repository-wide Ruff lint and formatting checks passed. The formatter also normalized existing Python examples in the profile/memory implementation plan so the repository's CI formatting gate passes.

GitHub's Ruff workflow also passed. Its leftover starter-template check rejected the already-tracked `uv.lock`; that template-only workflow is removed because IRA is a configured application with committed dependency versions. The lint and simulation workflows remain.

Review found and reproduced two additional ambiguous clock alternatives, including “tomorrow at 16:30 or 17:00.” Both now raise before deadline construction. The Windows-catalogue fixtures explicitly provide the platform-specific constant; all 13 launcher tests also passed with that constant initially absent from the simulated host.

The earlier evaluated-working-tree run passed 441 tests and skipped four live-provider tests. This clean branch excludes ten checks for unrelated uncommitted continuation/context features and adds two ambiguity regressions, so the counts are different.

Selected regression suites simulate native app launches, mouse/keyboard input, and providers. Task integration uses temporary SQLite stores. Live-provider flags remain disabled. No app restart or live IRA interaction is needed for publication.

## Evaluation status and limits

Before repairs, 25 of 33 planned live attempts were executed: nine PASS, five PARTIAL, nine FAIL, one UNVERIFIED, and one INVALID. Eight file/browser attempts remain held. These are uneven, small-sample results from one live session, not an overall intelligence rating. Generated conversational content was assessed from transcripts; audio delivery was not independently established.

Calculator launches, ambiguous-file clarification, and a constrained hypothetical schedule had demonstrated successes. Task deadlines, missing-app handling, and the file goal exposed the defects above. Arithmetic and notice comprehension had incomplete responses; JSON requests were refused. Generic system-error causes remain unconfirmed.

Semantic admission is not a formal safety guarantee. Registered app selection does not establish availability of portable or unregistered applications. Issuing a native launch does not prove a window appeared. Relative-date parsing is deliberately narrow, and restored policy checks need live latency/workflow testing.

After integration, preserve the original verdicts and run fresh typed, microphone-muted retests for deadlines and missing-app behavior. Scope-deviation and remaining file/browser workflows need a contained Windows environment. Live improvements, JSON delivery, and generic error recovery remain unverified.
