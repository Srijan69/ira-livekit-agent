"""Explicit procedural learning from verified evidence, without model spending."""

import re
from dataclasses import replace

from skill_format import parse_skill
from skill_store import skill_slug

_SECRET = re.compile(
    r"(?i)(bearer\s+[\w.-]+|(?:password|api[_-]?key|secret|token)\s*[=:]\s*\S+)"
)


def learn_from_run(run_id, name, store, run_store):
    record = run_store.get(run_id) if run_id else None
    if (
        not record
        or record.get("status") != "done"
        or record.get("verified") is not True
    ):
        raise ValueError("Learning requires a verified successful run.")
    if not name.strip() or len(name) > 100:
        raise ValueError("Use a skill name of 1 to 100 characters.")
    if store.get(name) is not None or store.versions(skill_slug(name)):
        raise ValueError(
            "That skill already exists. Choose a new name or explicitly edit it."
        )
    markdown = record.get("skill_markdown", "")
    if _SECRET.search(markdown):
        raise ValueError(
            "The procedure contains credentials; replace them with inputs before learning."
        )
    original = parse_skill(markdown)
    # Keep the parameterized source rather than copying observed page content,
    # tool results or the user's concrete input values into the procedure.
    candidate = replace(
        original,
        name=name.strip(),
        version=1,
        state="draft",
        pinned=False,
        source="learned",
        source_run_id=run_id,
    )
    return store.save(candidate, source="learned")
