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
