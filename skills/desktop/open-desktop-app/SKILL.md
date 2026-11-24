---
name: open-desktop-app
description: "Launch desktop applications on Windows and verify window visibility."
version: 1.0.0
author: "IRA Agent"
platforms: [windows]
metadata:
  hermes:
    category: desktop
    tags: [desktop, launch, windows, app]
  ira:
    task_kinds: ["open_app"]
    allowed_tools: ["open_app", "capture_screen", "list_desktop_windows"]
    allowed_effects: ["launch", "observe"]
    done_when: ["window_observed"]
    inputs: {"app_name": {"required": true, "type": "string"}}
---

# Open Desktop App Skill

Guides launching desktop applications on the Windows host and ensuring their window appears.

## When to Use
- The boss asks to open an application (Notepad, Calculator, VS Code, Discord, Terminal, etc.).
- Prioritize native desktop applications over web browser versions per the boss's preferences.

## Procedure
1. Call `open_app(app_name="<name>")` to launch the application.
2. Verify the application window opened by checking active window or window titles.
3. If the window does not appear within 8 seconds, use Windows search or inspect screen.
