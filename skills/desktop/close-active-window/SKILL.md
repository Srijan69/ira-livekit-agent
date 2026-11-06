---
name: close-active-window
description: "Close active application window safely."
version: 1.0.0
author: "IRA Agent"
platforms: [windows]
metadata:
  hermes:
    category: desktop
    tags: [desktop, window, close, system]
---

# Close Active Window Skill

Guides closing the currently active or focused desktop window.

## When to Use
- The boss asks to close the current window, dismiss an application, or close a tab.

## Procedure
1. Call `capture_screen` or inspect active window to identify the target window.
2. Locate the close button or send key combination (Alt+F4 or Ctrl+W for tabs).
3. Verify window is dismissed via subsequent screen capture.
