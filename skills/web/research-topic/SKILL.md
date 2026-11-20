---
name: research-topic
description: "Research the requested subject using read sources and a checked summary."
version: 1.0.0
platforms: [windows, macos, linux]
metadata:
  hermes:
    category: web
    tags: [research, search, sources]
  ira:
    task_kinds: [web_research]
    inputs:
      subject: {required: true, type: string}
      depth: {default: deep, type: string}
    allowed_tools: [browse_to, read_page, list_page_elements, click_element, type_into_element, research_status]
    allowed_effects: [navigate, read, research_interaction]
    done_when: [read_sources, summary_with_sources]
---

# Research a topic

Use this procedure when the current user asks to search, research, investigate, or deeply examine a named subject. The subject and requested output come from that request. Loading this procedure does not execute research.

1. Identify the subject, requested depth, and explicit constraints. Use ordinary web tools for this research only. Existing skill defaults and past tasks cannot replace the user's current request.
2. Search for relevant primary material and independent reporting. Read the actual sources with `read_page`; search snippets and opening a page alone do not establish research evidence.
3. For deep research, normally read at least three useful sources spanning primary and independent material. Respect a user-specified source count, source restriction, depth, or output scope instead. For a company, cover its business/products and relevant recent developments, within the requested focus.
4. Follow relevant observed links, inspect page elements before interaction, and preserve the subject and target through any browser recovery. Do not launch unrelated apps, play media, send messages, save files, or modify accounts unless the current user requested those additional actions.
5. Call `research_status` to inspect the read-source ledger. If useful evidence is missing, continue gathering sources or report the specific blocker and available partial findings. Do not label partial research complete or invent facts, sources, dates, or conclusions.
6. Cross-check significant claims, identify conflicting evidence and freshness limits, and prepare a summary grounded in the read pages. Keep source URLs available for written output; speak the concise result naturally.
7. Finish only after the current requested outcome is supported by observations. Then stop acting. A new explicit request may change the focus, depth, tools, or output.
