# Frontend theme handoff — 2026-10-16

Settings now contains Themes with Holographic Blue, Arctic Silver, Neon Red, Cyberpunk Pink, Solar Gold and Neon Green. Selecting a different card saves the choice, displays a restart screen, and requests a desktop relaunch.

## Frontend contract

- `dashboard/src/lib/themes.ts`: palette registry and localStorage key `ira.theme.v1`. `main.tsx` applies it before React mounts.
- `dashboard/src/lib/themeAssets.ts` and `dashboard/assets/themes/`: bundled previews and clean backgrounds. No network request is required to load a theme.
- `useTheme.ts`, `Settings.tsx`, `ThemeRestart.tsx`: selection, duplicate-click guard, error recovery and restart UI.
- Shared CSS colors drive the existing components; waveform Canvas resolves its CSS color when mounted.

## Desktop boundary

`window.ira.restart(): Promise<void>` invokes `ira:app:restart`. This is separate from the existing `window.ira.agent.restart()` API.

Main flushes Chromium storage, prepares the next launch and quits. The existing supervisor stops the owned Python processes during `before-quit`; its behavior and Python code were not changed. Built/packaged launches use Electron `app.relaunch()`. Development launches use a hidden Node handoff to restart electron-vite at the same origin. Browser-only preview reloads its page instead.

No Python/backend API, LiveKit tool, agent instruction or workflow was added by this feature. External-agent ownership continues to use the existing `IRA_EXTERNAL_AGENT` behavior.

## Verification

152 frontend tests pass; TypeScript checks and the production Electron build pass. All six choices were exercised in the browser. Native restart ordering and actual electron-vite configuration resolution are covered by tests; a live voice-session restart was not run.

The current desktop build is in `dashboard/out/`. Reopen IRA to load it. Visual evidence and scope are in `dashboard/design-qa.md`; the browser preview runs at `http://127.0.0.1:5273/`.
