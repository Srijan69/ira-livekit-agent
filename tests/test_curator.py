"""Tests for Hermes-aligned SkillCurator in IRA."""

import time
from pathlib import Path

from curator import SkillCurator
from skill_format import Action, Check, Skill, Step
from skill_store import SkillStore


def _make_skill(name: str, pinned: bool = False) -> Skill:
    return Skill(
        name=name,
        description=f"Description for {name}",
        goal=f"Complete {name}",
        steps=(
            Step(
                number=1,
                purpose="Step 1",
                action=Action(kind="tool", tool="system_status"),
                check=Check(kind="window_title", value="Status"),
            ),
        ),
        pinned=pinned,
        state="enabled",
    )


def test_suggest_inactive_and_failing_skills(tmp_path: Path):
    store = SkillStore(tmp_path)
    curator = SkillCurator(store)

    store.save(_make_skill("Active Skill"))
    store.save(_make_skill("Stale Skill"))
    store.save(_make_skill("Archive Candidate"))
    store.save(_make_skill("Failing Skill"))
    store.save(_make_skill("Pinned Stale Skill", pinned=True))

    now_ms = time.time() * 1000
    ms_day = 86_400_000

    # Simulate activity ages
    with store._transaction() as db:
        # Active: 2 days old
        db.execute(
            "UPDATE skills SET last_activity=? WHERE slug=?",
            (int(now_ms - 2 * ms_day), "active-skill"),
        )
        # Stale: 16 days old
        db.execute(
            "UPDATE skills SET last_activity=? WHERE slug=?",
            (int(now_ms - 16 * ms_day), "stale-skill"),
        )
        # Archive candidate: 35 days old
        db.execute(
            "UPDATE skills SET last_activity=? WHERE slug=?",
            (int(now_ms - 35 * ms_day), "archive-candidate"),
        )
        # Failing skill: 4 fails, 1 success
        db.execute(
            "UPDATE skills SET failure_count=4, success_count=1, last_activity=? WHERE slug=?",
            (int(now_ms - 1 * ms_day), "failing-skill"),
        )
        # Pinned stale: 40 days old
        db.execute(
            "UPDATE skills SET last_activity=? WHERE slug=?",
            (int(now_ms - 40 * ms_day), "pinned-stale-skill"),
        )

    suggestions = curator.suggest(now=now_ms)
    slug_to_action = {s["slug"]: s["action"] for s in suggestions}

    assert "active-skill" not in slug_to_action
    assert slug_to_action["stale-skill"] == "review"
    assert slug_to_action["archive-candidate"] == "consider_archive"
    assert slug_to_action["failing-skill"] == "inspect_failures"
    assert "pinned-stale-skill" not in slug_to_action  # Pinned is protected


def test_apply_automatic_transitions_archives_inactive(tmp_path: Path):
    store = SkillStore(tmp_path)
    curator = SkillCurator(store)

    store.save(_make_skill("Keep Active"))
    store.save(_make_skill("Auto Archive Me"))
    store.save(_make_skill("Pinned Never Archive", pinned=True))

    now_ms = time.time() * 1000
    ms_day = 86_400_000

    with store._transaction() as db:
        db.execute(
            "UPDATE skills SET last_activity=? WHERE slug=?",
            (int(now_ms - 1 * ms_day), "keep-active"),
        )
        db.execute(
            "UPDATE skills SET last_activity=? WHERE slug=?",
            (int(now_ms - 32 * ms_day), "auto-archive-me"),
        )
        db.execute(
            "UPDATE skills SET last_activity=? WHERE slug=?",
            (int(now_ms - 35 * ms_day), "pinned-never-archive"),
        )

    result = curator.apply_automatic_transitions(now=now_ms)
    assert result["archived"] == 1
    assert "auto-archive-me" in result["archived_slugs"]

    # Verify auto-archive-me is archived in store
    assert store.get("Auto Archive Me") is None
    archived_list = [s.slug for s in store.list(include_archived=True) if s.archived]
    assert "auto-archive-me" in archived_list

    # Verify pinned skill was NOT archived
    assert store.get("Pinned Never Archive") is not None

    # Verify recovery via restore
    restored = store.restore("Auto Archive Me")
    assert restored is not None
    assert store.get("Auto Archive Me") is not None


def test_curator_paused_state(tmp_path: Path):
    store = SkillStore(tmp_path)
    curator = SkillCurator(store)

    assert curator.is_paused() is False
    curator.set_paused(True)
    assert curator.is_paused() is True

    # When paused, apply_automatic_transitions skips work
    result = curator.apply_automatic_transitions()
    assert result["status"] == "paused"
    assert result["archived"] == 0


def test_find_consolidation_candidates(tmp_path: Path):
    store = SkillStore(tmp_path)
    curator = SkillCurator(store)

    store.save(_make_skill("spotify-play-rock"))
    store.save(_make_skill("spotify-play-jazz"))
    store.save(_make_skill("spotify-search-artist"))
    store.save(_make_skill("browser-open-url"))

    candidates = curator.find_consolidation_candidates()
    assert len(candidates) == 1
    cluster = candidates[0]
    assert cluster["cluster_prefix"] == "spotify"
    assert cluster["skill_count"] == 3
    assert "spotify-play-rock" in cluster["slugs"]
