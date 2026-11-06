---
name: log-error
description: "Record structured errors and diagnostics for system issues."
version: 1.0.0
author: "IRA Agent"
platforms: [windows, macos, linux]
metadata:
  hermes:
    category: system
    tags: [diagnostics, logging, error, telemetry]
---

# Log Error Skill

Guides capturing and recording unexpected exceptions, failed actions, or degraded state into system logs.

## When to Use
- A tool execution fails unexpectedly or a host action times out.
- The user reports an anomaly requiring diagnostic capture.

## Procedure
1. Capture error details: tool name, arguments, exception message, timestamp.
2. Record to local application error log via error logger.
3. Inform the boss concisely with actionable guidance.
