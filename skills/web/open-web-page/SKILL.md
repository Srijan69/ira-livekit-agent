---
name: open-web-page
description: "Navigate to a web URL and verify page content."
version: 1.0.0
author: "IRA Agent"
platforms: [windows, macos, linux]
metadata:
  hermes:
    category: web
    tags: [web, browser, navigate, url]
  ira:
    task_kinds: ["navigate"]
    allowed_tools: ["browse_to", "read_page", "list_page_elements"]
    allowed_effects: ["navigate", "read"]
    done_when: ["page_read"]
    inputs: {"url": {"required": true, "type": "string"}}
---

# Open Web Page Skill

Guides navigating the browser to specific web URLs and verifying expected page elements or text.

## When to Use
- The boss asks to open a website, check a URL, or navigate to a destination.

## Procedure
1. Call `browse_to(url="<target_url>")`.
2. Wait for page load and call `read_page` or `list_page_elements` to verify content.
