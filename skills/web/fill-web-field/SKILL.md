---
name: fill-web-field
description: "Input text into form fields or search boxes on web pages."
version: 1.0.0
author: "IRA Agent"
platforms: [windows, macos, linux]
metadata:
  hermes:
    category: web
    tags: [web, forms, input, browser]
  ira:
    task_kinds: ["fill_field"]
    allowed_tools: ["browse_to", "list_page_elements", "type_into_element", "read_page"]
    allowed_effects: ["navigate", "type", "read"]
    done_when: ["field_value_observed"]
    inputs: {"url": {"required": true, "type": "string"}, "text": {"required": true, "type": "string"}}
---

# Fill Web Field Skill

Guides locating form fields on web pages and entering text safely.

## When to Use
- Filling in search inputs, form text fields, or web inputs on reviewed web pages.

## Procedure
1. Navigate to target URL using `browse_to`.
2. Inspect available elements with `list_page_elements` to find the exact selector or element ID.
3. Call `type_into_element` with the selector and text.
4. Verify the value using `read_page` or element inspection before submission.
