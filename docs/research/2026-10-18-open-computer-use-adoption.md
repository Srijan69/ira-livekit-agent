# Open Computer Use: fit for IRA's next execution milestone

Research date: 2026-10-18. Repository: [iFurySt/open-computer-use](https://github.com/iFurySt/open-computer-use).

Source is pinned to commit [`5136415f52b87f419227bae28767cf469a6bc3b5`](https://github.com/iFurySt/open-computer-use/commit/5136415f52b87f419227bae28767cf469a6bc3b5), committed 2026-10-18 04:20:37 UTC. This is the merge of Windows input PR #97. Public GitHub API and raw source were inspected; no external repository scripts or binaries were executed, and no package was installed.

## Recommendation

Adopt its observation and input-validation ideas in IRA's existing typed browser/desktop adapters. Do not make the complete OCU runtime a dependency of this milestone. OCU is a native desktop-control service with MCP and an optional JavaScript interface; it does not provide IRA's durable skill runs, expected-outcome verification, clarification pause/resume, or voice orchestration. Its browser-related README link points to a separate project. IRA already has Selenium browser control, desktop primitives, a serialized execution service, and typed outcomes. [S1, S2, I1]

An optional Windows UI Automation adapter would be a reasonable later experiment when app-specific desktop targeting becomes necessary. Place it behind the existing execution service and allowlisted native tools. A long-lived native MCP process can preserve observation state; independent one-shot calls cannot be assumed to preserve element indices. Avoid exposing OCU's arbitrary JavaScript REPL to the voice model merely to obtain UI Automation. [S3, S4, S9]

## What the implementation actually contains

| Area | Source-backed behavior | Consequence for IRA |
| --- | --- | --- |
| License | MIT, copyright 2026 Leo; copied or substantial portions must retain the copyright and permission notice. [S5] | Design ideas can be applied directly; retain attribution if copying implementation. |
| Platforms | macOS Swift is the main runtime. Architecture explicitly calls Windows and Linux experimental. Windows uses Go with an embedded PowerShell UI Automation/Win32 bridge; Linux uses Go with a Python AT-SPI bridge. [S2] | Neither Linux nor macOS implementation is a Windows drop-in. |
| Model dependency | Native MCP dispatch accepts explicit tool calls and returns text/images; it contains no model inference loop. Optional JavaScript bindings wrap that MCP interface. [S3, S4] | IRA can retain its existing LLM provider; screenshots only need a vision model when IRA chooses visual interpretation. |
| Windows prerequisites | `powershell.exe`, .NET UI Automation/System.Drawing, and the signed-in interactive desktop. Each bridge call starts PowerShell with a 30-second process timeout. The npm launcher needs Node 18+. [S1, S3, S6] | Desktop availability and latency must be measured locally. A server process without an interactive desktop is not equivalent. |
| Linux prerequisites | PyGObject/AT-SPI, desktop D-Bus/XDG session, optional GDK capture. Wayland screenshot/input behavior is best effort; black captures are omitted. [S7, S9] | Linux support does not establish Windows compatibility or universal Wayland control. |
| Tools | App listing/state, click, secondary action, scroll, drag, type, key, value setting; macOS has additional capabilities such as targeted `query`. [S1, S3] | Keep IRA's browser DOM interface and desktop interface distinct. |

The latest published GitHub release at inspection was [v1.1.2](https://github.com/iFurySt/open-computer-use/releases/tag/v1.1.2), published 2026-10-17 07:23:04 UTC. Its commit is `531c9d4d6e2e842f93368283ebd1d757625b73b1`. The researched source is [six commits ahead](https://github.com/iFurySt/open-computer-use/compare/v1.1.2...5136415f52b87f419227bae28767cf469a6bc3b5); do not assume a release/npm install contains the new guarded Windows input behavior.

## Observation, action, and verification

The Windows service retains app snapshots keyed by name/identifier/PID. `get_app_state` returns UIA records, PID, HWND, window bounds, focused/selected text, and a screenshot when usable. Actions require an existing observation and carry its process/window identity into the bridge. The bridge rejects a changed PID/HWND before dispatch, waits 120 ms after an action, and builds another snapshot. Bridge errors become MCP `isError: true`; the JavaScript wrapper converts these into exceptions. [S3, S4, S6]

This provides useful observations, not proof of task completion. For example, returning a new snapshot after typing does not assert that the intended field contains the requested text. IRA must retain a separate postcondition such as exact field value, selected state, matching URL, or expected visible text. An attempted action followed by an unavailable observer must remain unverified. [S3, I1]

Selective observation can request UIA only or screenshot only. Screenshot-only observation invalidates old element indices; UIA-only observation cannot support screenshot-coordinate input until a usable screenshot is refreshed. The implementation bounds text/tree traversal and omits unusable Windows screenshots instead of inventing a placeholder. [S3, S8]

Windows capture uses `Graphics.CopyFromScreen` for the window rectangle. It therefore captures the desktop pixels in that rectangle, including any overlapping window, rather than providing a guaranteed unobscured rendering of the target. The physical input path separately checks which window is under the intended point. Do not assume the screenshot and UIA tree always describe identical visible content. [S6]

## Small patterns worth adopting now

1. **Bind an action to a fresh observation.** Carry an observation ID and browser document/window identity with any ephemeral element index. Invalidate it after navigation, mutation, or a different observation. Reject expired or missing observations instead of silently re-enumerating a different target. A saved reusable skill should use a stable selector/description resolved at execution, not a saved numeric index. [S3, S8, I1]
2. **Require a unique target.** Refuse ambiguous matches and ask for the missing choice. OCU Windows `Find-Element` first matches runtime ID but can fall back to the first same name/AutomationId and control type. Its physical fallback applies an additional strict runtime-ID/frame check. IRA should use strict uniqueness for its new browser/desktop paths rather than copying the broad fallback. [S6]
3. **Keep delivery modes explicit.** OCU prefers UIA, then app messages; physical input is separately enabled. Explicit `app_post`/`accessibility` does not silently become global input. For IRA browser clicks, an intercepted click should not silently turn into a JavaScript click that bypasses an overlay. Surface the obstruction or unresolved state to the execution service. [S6, S10, I1]
4. **Guard physical input with current evidence.** Recheck target HWND/PID, physical pixel bounds, foreground, and the window hit by the coordinate immediately before input. Reject moved, covered, minimized, or replaced targets. Validate a complete key chord before pressing anything; release all keys/buttons pressed by the operation in `finally`. [S6, S10]
5. **Separate action delivery from outcome truth.** Return typed delivery evidence, then evaluate the skill's expected result. For typing, read back the intended field's value; for clicking, check the specified state transition. Keep observation collection bounded and collect a screenshot only when it supplies necessary evidence. [S3, S8, I1]
6. **Use an owned test fixture with independent state.** OCU's Windows WPF fixture exports state separately from the action response. IRA can test a harmless local browser form and owned desktop fixture, asserting changed values/counters and refusal on stale targets. This is stronger than asserting only that the input function was called. [S10, S11]

Clarification belongs in IRA's service: persist the pending question and expected answer shape, retain the exact skill version and step, validate the answer, then resume only after refreshing any affected UI evidence. OCU's inspected native tool list and Windows dispatcher contain no clarification/pause/resume protocol. Retain IRA's cancellation and uncertain-outcome rules when a clarification overlaps a still-running operation. [S3, I1]

## Boundaries and remaining evidence

- OCU's JavaScript REPL is arbitrary local JavaScript with the launching user's filesystem, environment, module, and network access. Worker timeout/reset is lifecycle management, not a sandbox. Native discrete tools are the narrower integration surface. [S9]
- Upstream documents that session approval/dynamic app policy is incomplete. Its documented password-manager bundle denylist must not be assumed to protect Windows: the inspected Windows `Resolve-App` and `List-Apps` paths do not apply that bundle denylist. IRA must retain its own policy and allowed capabilities. [S6, S9]
- Physical Windows input can move the real pointer and change focus, and remains subject to Windows integrity/desktop restrictions. A process killed during input cannot guarantee `finally` cleanup. App messages can be ignored by particular toolkits; several message paths ignore `PostMessage`'s return value, another reason to retain independent outcome checks. [S6, S10]
- Upstream records successful hosted WPF/native MCP/Worker-JS tests, including stale element and occlusion refusal, at 96 DPI. That report explicitly does not establish 125%/150%, WinUI, or multi-monitor acceptance; the contributor's separate 150% report is distinct evidence. The checked-in CI workflow runs Windows observation and input fixture tests. These are upstream results, not tests run in this IRA workspace. [S10, S11]
- No local performance or live-app compatibility measurements were made for OCU. A future adapter experiment should measure per-action latency, cancellation behavior, DPI/multi-monitor coordinates, Unicode text, and the actual target application's UIA support before promotion.

## Sources

All source links below are pinned to the inspected commit.

- [S1: README, runtime interfaces and Node requirement](https://github.com/iFurySt/open-computer-use/blob/5136415f52b87f419227bae28767cf469a6bc3b5/README.md#L14-L197)
- [S2: Architecture and platform split](https://github.com/iFurySt/open-computer-use/blob/5136415f52b87f419227bae28767cf469a6bc3b5/docs/ARCHITECTURE.md#L1-L34)
- [S3: Windows Go service, observation cache, actions, process timeout and MCP dispatch](https://github.com/iFurySt/open-computer-use/blob/5136415f52b87f419227bae28767cf469a6bc3b5/apps/OpenComputerUseWindows/main.go#L393-L684)
- [S4: JavaScript native-tool adapter](https://github.com/iFurySt/open-computer-use/blob/5136415f52b87f419227bae28767cf469a6bc3b5/scripts/node-repl/open-computer-use-repl.mjs#L288-L412)
- [S5: MIT license](https://github.com/iFurySt/open-computer-use/blob/5136415f52b87f419227bae28767cf469a6bc3b5/LICENSE)
- [S6: Windows PowerShell runtime](https://github.com/iFurySt/open-computer-use/blob/5136415f52b87f419227bae28767cf469a6bc3b5/apps/OpenComputerUseWindows/runtime.ps1) — physical guards 320–390; key cleanup 547–608; app resolution 611–650; capture 873–894; snapshots/listing 945–991; element resolution 1019–1043; action dispatch/read-back 1251–1376.
- [S7: Linux Python runtime and desktop requirements](https://github.com/iFurySt/open-computer-use/blob/5136415f52b87f419227bae28767cf469a6bc3b5/apps/OpenComputerUseLinux/runtime.py#L14-L69)
- [S8: Selective observation and invalidation](https://github.com/iFurySt/open-computer-use/blob/5136415f52b87f419227bae28767cf469a6bc3b5/docs/selective-observation.md)
- [S9: Security boundaries](https://github.com/iFurySt/open-computer-use/blob/5136415f52b87f419227bae28767cf469a6bc3b5/docs/SECURITY.md#L3-L42)
- [S10: Windows input and documented acceptance scope](https://github.com/iFurySt/open-computer-use/blob/5136415f52b87f419227bae28767cf469a6bc3b5/docs/windows-input.md)
- [S11: Windows PR validation report](https://github.com/iFurySt/open-computer-use/blob/5136415f52b87f419227bae28767cf469a6bc3b5/docs/exec-plans/completed/20261018-pr97-windows-input.md#L28-L43) and [checked-in CI](https://github.com/iFurySt/open-computer-use/blob/5136415f52b87f419227bae28767cf469a6bc3b5/.github/workflows/windows-observation.yml).
- I1: IRA source inspected before this milestone's concurrent edits: `src/browser.py` (`observe`, `list_elements`, `_resolve`, `click`, `type_text`), `src/desktop.py`, `src/skill_adapters.py`, `src/skill_execution.py`, and `src/skill_tools.py`. These local files are intentionally not pinned to an uncommitted workspace hash; recommendations above distinguish existing behavior from proposed additions.
