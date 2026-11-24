"""Explicit procedural learning from verified evidence, without model spending."""

import re
from dataclasses import replace

from skill_format import parse_skill
from skill_store import skill_slug

_SECRET = re.compile(
    r"(?i)(bearer\s+[\w.-]+|(?:password|api[_-]?key|secret|token)\s*[=:]\s*\S+)"
)


def learn_from_run(run_id, name, store, run_store, *, catalog=None):
    record = run_store.get(run_id) if run_id else None
    if (
        not record
        or record.get("status") != "done"
        or record.get("verified") is not True
    ):
        raise ValueError("Learning requires a verified successful run.")
    if not name.strip() or len(name) > 100:
        raise ValueError("Use a skill name of 1 to 100 characters.")
    if record.get("mode") == "procedure":
        if (
            not record.get("request_id")
            or record.get("decision", {}).get("request_id") != record["request_id"]
            or not record.get("steps")
            or not record.get("document_digest")
        ):
            raise ValueError(
                "Learning requires verified evidence for the original task."
            )
        if catalog is None:
            raise ValueError("An adaptive procedure catalog is required.")
        if catalog.get_skill(name) is not None:
            raise ValueError(
                "That skill already exists. Choose a new name or explicitly edit it."
            )
        import yaml

        from skills import parse_frontmatter

        markdown = record.get("skill_markdown", "")
        if _SECRET.search(markdown):
            raise ValueError(
                "The procedure contains credentials; use parameterized inputs."
            )
        metadata, body = parse_frontmatter(markdown)
        metadata.update(name=name.strip(), version="1.0.0")
        metadata.setdefault("metadata", {}).setdefault("ira", {})["source_run_id"] = (
            run_id
        )
        category = (
            metadata.get("metadata", {}).get("hermes", {}).get("category", "learned")
        )
        text = (
            "---\n"
            + yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True)
            + "---\n\n"
            + body
        )
        created = catalog.create_skill(name, text, category=category)
        if not created["success"]:
            raise ValueError(created["error"])
        return catalog.get_skill(name)
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
