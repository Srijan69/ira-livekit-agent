# Hermes skill execution repair

**Goal:** IRA loads ordinary Hermes SKILL.md procedures, executes available tools under the current human request, and reports observed results. Regression case: play Aaya Sher on Spotify using Brave only.

**Architecture:** `skills/` remains the procedural library, with compact discovery and on-demand instruction/support-file retrieval. The model follows procedures adaptively; loading one is never reported as execution. `workflows/` remains a separate optional compiled runner. Voice and dashboard discovery identify each mode. Browser actions enforce the Spotify browser preference and verify the requested track, not merely a playing button.

**Constraints:** Preserve prior uncommitted profile/context work; Boss/Sir addressing; current-request identity, cancellation and uncertain-effect guards remain. No real messaging in evaluations. No copying browser profiles or fallback to another browser. Use uv for Python checks.

- [x] Reproduce catalog/runner mismatch, missing Spotify schemas, and JSON string corruption through failing tests.
- [x] Correct catalog routing in `agent.py`, `skill_tools.py`, `skills.py`, and both instruction policies. Test procedural load versus compiled execution, ambiguity and supporting-file boundaries.
- [x] Restore voice Spotify tools in `spotify_tools.py`; enforce fresh browser preference. Fix selection/state verification in `browser.py`, including unavailable Brave and failed navigation. Test negative playback and wrong-track cases.
- [x] Use Hermes's bounded no-tool continuation pattern only for a current, finalized request with an acknowledged action or false capability refusal, no effects, no blocker or clarification, and no interruption. Test closure, cancellation and retry bounds.
- [x] Expose procedural mode in `skill_bridge.py` and dashboard Skills with readable procedures and correctly scoped Run requests. Validate shared contract consumers.
- [x] Run offline regression suites and controlled SDK/provider evaluation. Inspect actual Brave Spotify state and record any login/network/provider blocker distinctly from successful playback.
- [x] Write measured evaluation evidence and audit each requested outcome before goal completion.

**Verification:** 1,372 backend tests passed (48 skipped); 365 dashboard tests,
TypeScript checks and Electron production build passed. Sixteen isolated Brave
DOM evaluations passed, the final four-case Gemini Live tool-routing sample
passed, and the real scoped Spotify tool verified Aaya Sher by Anirudh
Ravichander in Brave with advancing playback. Evaluation details and limits are
in `docs/research/2026-11-07-hermes-execution-evaluation.md`.

Reference: locally extracted `C:/Users/kommi/Downloads/hermes-agent-main/tools/skills_tool.py` and `agent/conversation_loop.py`; this directory has no Git revision. LiveKit workflows/tools documentation governs SDK integration.
