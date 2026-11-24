"""Targeted migration of retired operating instructions, never journal history."""

import re

from personal_memory import MemoryConflict


def retire_music_recipes(legacy, profile):
    changed = 0
    for target in ("memory", "user"):
        for entry in legacy.list_entries(target):
            recipe = bool(
                re.search(r"\b(?:when asked|every time you are asked)\b", entry, re.I)
                and re.search(r"\b(?:play|playback)\b", entry, re.I)
                and re.search(r"\b(?:spotify|song|playlist)\b", entry, re.I)
            )
            if recipe:
                result = legacy.remove(target, entry)
            elif "spotify playback" in entry.casefold():
                replacement = re.sub(r"\bSpotify playback,?\s*", "", entry, flags=re.I)
                result = legacy.replace(target, entry, replacement)
            else:
                continue
            if not result.get("success"):
                raise ValueError(
                    "Operating memory changed during retirement; rerun after refreshing."
                )
            changed += 1
    profile_changed = False
    for attempt in range(3):
        current = profile.get_profile()
        preferences = re.sub(
            r"\bSpotify:\s*[^.\n;]*(?:[.;](?:\s|$))?",
            "",
            current["preferences"],
            flags=re.I,
        ).strip()
        if preferences == current["preferences"]:
            break
        try:
            profile.save_profile(
                {"preferences": preferences},
                expected_revision=current["revision"],
                source="explicit",
            )
        except MemoryConflict:
            if attempt == 2:
                raise
        else:
            profile_changed = True
            break
    return {"operating_entries_changed": changed, "profile_changed": profile_changed}
