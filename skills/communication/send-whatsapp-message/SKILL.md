---
name: send-whatsapp-message
description: "Send WhatsApp messages via WhatsApp Web or desktop application."
version: 1.0.0
author: "IRA Agent"
platforms: [windows]
metadata:
  hermes:
    category: communication
    tags: [whatsapp, messaging, chat, communication]
  ira:
    task_kinds: ["send_message"]
    allowed_tools: ["browse_to", "list_page_elements", "type_into_element", "click_element", "read_page", "capture_screen"]
    allowed_effects: ["navigate", "type", "send", "observe"]
    done_when: ["recipient_verified", "message_observed"]
    inputs: {"recipient": {"required": true, "type": "string"}, "message": {"required": true, "type": "string"}}
---

# Send WhatsApp Message Skill

Guides sending messages via WhatsApp Web or desktop client using verified credentials.

## When to Use
- The boss requests sending a WhatsApp message to a specific contact or phone number.

## Procedure
1. Open the requested WhatsApp desktop application or navigate to https://web.whatsapp.com for WhatsApp Web.
2. Search for the contact by name in the search bar.
3. Select the contact from the search results.
4. Type the message and submit it by pressing Enter or clicking the Send button.
5. Verify transmission with screen capture to confirm the message bubble and checkmark appear.
