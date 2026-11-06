"""The versioned skill store.

Every test runs against a ``tmp_path`` folder: the store writes and deletes
files, and a suite that rewrote the boss's real workflows/ folder is one
nobody could run while working.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

import skill_store
from skill_format import Action, Skill, Step, parse_skill, render_skill
from skill_store import SkillStore

LEGACY_PLAYLIST = """\
---
name: Play Favorite Playlist
goal: Play the boss's favorite funk songs playlist in Brave browser.
version: 1
allowed_tools: open_app, browse_to, spotify_play_playlist
---

## Steps

1. open_app: app_name="Brave"
2. browse_to: url="https://open.spotify.com"
"""


def make_skill(
    name: str = "Open Notes", *, purpose: str = "Open the notes app"
) -> Skill:
    return Skill(
        name=name,
        description="Opens the notes app.",
        steps=(
            Step(
                1,
                purpose,
                action=Action("tool", tool="open_app", args=(("app_name", "Notepad"),)),
            ),
        ),
    )


@pytest.fixture
def store(tmp_path: Path) -> SkillStore:
    return SkillStore(tmp_path)


class TestSaving:
    def test_writes_a_canonical_file_named_after_the_skill(
        self, store: SkillStore, tmp_path: Path
    ) -> None:
        saved = store.save(make_skill(), source="dictated")

        path = tmp_path / "open-notes.md"
        assert path.exists()
        assert parse_skill(path.read_text(encoding="utf-8")) == saved
        assert (saved.version, saved.source) == (1, "dictated")

    def test_saving_again_makes_a_new_version_and_keeps_the_old_one(
        self, store: SkillStore
    ) -> None:
        store.save(make_skill(), source="dictated")

        second = store.save(make_skill(purpose="Open Notepad"), source="corrected")

        assert (second.version, second.source) == (2, "corrected")
        assert store.versions("Open Notes") == [1, 2]
        assert store.version("Open Notes", 1).steps[0].purpose == "Open the notes app"

    def test_refuses_a_skill_that_would_not_parse_back(
        self, store: SkillStore, tmp_path: Path
    ) -> None:
        # A save that wrote an unreadable file would lose the skill on reload.
        broken = Skill(name="Bad", description="", steps=())

        with pytest.raises(ValueError):
            store.save(broken, source="dictated")
        assert not list(tmp_path.glob("*.md"))


class TestFinding:
    def test_finds_by_name_slug_or_any_capitalisation(self, store: SkillStore) -> None:
        store.save(make_skill(), source="dictated")

        assert store.get("Open Notes").name == "Open Notes"
        assert store.get("open-notes").name == "Open Notes"
        assert store.get("OPEN NOTES").name == "Open Notes"
        assert store.get("something else") is None

    def test_finds_a_hand_made_file_whose_name_differs_from_its_filename(
        self, store: SkillStore, tmp_path: Path
    ) -> None:
        (tmp_path / "my-file.md").write_text(
            render_skill(make_skill("Morning Routine")), encoding="utf-8"
        )

        assert store.get("Morning Routine").name == "Morning Routine"

    def test_lists_valid_skills_and_explains_broken_ones(
        self, store: SkillStore, tmp_path: Path
    ) -> None:
        store.save(make_skill(), source="dictated")
        (tmp_path / "broken.md").write_text(
            "---\nname: Broken\ndescription: Has a typo.\n---\n\n## Steps\n1. **Go**\n   - do: wait forever\n",
            encoding="utf-8",
        )

        listed = store.list()

        assert [entry.skill.name for entry in listed if entry.skill] == ["Open Notes"]
        broken = next(entry for entry in listed if entry.skill is None)
        assert broken.slug == "broken"
        assert "wait 10s" in str(broken.issues[0])

    def test_rejects_tools_the_registry_does_not_know(self, tmp_path: Path) -> None:
        SkillStore(tmp_path).save(make_skill(), source="dictated")

        strict = SkillStore(tmp_path, known_tools={"browse_to"})

        assert strict.get("Open Notes") is None
        assert "unknown tool 'open_app'" in str(strict.issues("Open Notes")[0])


class TestUndo:
    def test_restores_the_previous_version_as_a_new_one(
        self, store: SkillStore
    ) -> None:
        # History only grows: undo is itself a change you can undo.
        store.save(make_skill(), source="dictated")
        store.save(make_skill(purpose="Open Notepad"), source="corrected")

        restored = store.undo("Open Notes")

        assert restored.version == 3
        assert restored.steps[0].purpose == "Open the notes app"
        assert store.versions("Open Notes") == [1, 2, 3]

    def test_cannot_undo_a_skill_with_no_earlier_version(
        self, store: SkillStore
    ) -> None:
        store.save(make_skill(), source="dictated")

        assert store.undo("Open Notes") is None


class TestDeleting:
    def test_removes_the_skill_but_keeps_its_history(
        self, store: SkillStore, tmp_path: Path
    ) -> None:
        store.save(make_skill(), source="dictated")

        assert store.delete("Open Notes") is True

        assert store.get("Open Notes") is None
        assert not (tmp_path / "open-notes.md").exists()
        assert store.versions("Open Notes") == [1]

    def test_deleting_something_missing_reports_it(self, store: SkillStore) -> None:
        assert store.delete("Nothing") is False


class TestLegacyAndHandEdits:
    def test_reads_an_old_workflow_file_without_touching_it(
        self, store: SkillStore, tmp_path: Path
    ) -> None:
        path = tmp_path / "play-favorite-playlist.md"
        path.write_text(LEGACY_PLAYLIST, encoding="utf-8")

        skill = store.get("Play Favorite Playlist")

        assert skill.steps[0].action.args == (("app_name", "Brave"),)
        assert path.read_text(encoding="utf-8") == LEGACY_PLAYLIST

    def test_migration_rewrites_old_files_and_keeps_the_original(
        self, store: SkillStore, tmp_path: Path
    ) -> None:
        (tmp_path / "play-favorite-playlist.md").write_text(
            LEGACY_PLAYLIST, encoding="utf-8"
        )

        migrated = store.migrate_legacy()

        assert migrated == ["Play Favorite Playlist"]
        skill = store.get("Play Favorite Playlist")
        assert (skill.version, skill.source) == (2, "compiled")
        assert store.version_text("Play Favorite Playlist", 1) == LEGACY_PLAYLIST
        assert store.migrate_legacy() == []

    def test_notices_a_hand_edit_and_records_it_as_a_version(
        self, store: SkillStore, tmp_path: Path
    ) -> None:
        store.save(make_skill(), source="dictated")
        path = tmp_path / "open-notes.md"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "Open the notes app", "Open my notes"
            ),
            encoding="utf-8",
        )

        edited = store.get("Open Notes")

        assert (edited.version, edited.source) == (2, "edited")
        assert edited.steps[0].purpose == "Open my notes"
        assert store.versions("Open Notes") == [1, 2]

    def test_a_broken_hand_edit_is_reported_not_recorded(
        self, store: SkillStore, tmp_path: Path
    ) -> None:
        store.save(make_skill(), source="dictated")
        path = tmp_path / "open-notes.md"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "do: open_app", "do: wait forever #"
            ),
            encoding="utf-8",
        )

        assert store.get("Open Notes") is None
        assert store.issues("Open Notes")
        assert store.versions("Open Notes") == [1]


class TestLifecycle:
    def test_archive_restore_and_pin_survive_reopening(
        self, store: SkillStore, tmp_path: Path
    ) -> None:
        store.save(make_skill())
        assert store.set_pinned("open-notes", True).pinned is True
        assert store.archive("Open Notes") is True
        reopened = SkillStore(tmp_path)
        assert reopened.get("Open Notes") is None
        assert reopened.catalog() == []
        archived = reopened.catalog(include_archived=True)
        assert len(archived) == 1
        assert archived[0]["archived"] is True
        assert archived[0]["pinned"] is True
        assert reopened.restore("open-notes").pinned is True
        assert reopened.get("Open Notes").version == 2
        assert reopened.versions("open-notes") == [1, 2]

    def test_catalog_includes_drafts_but_not_full_steps(
        self, store: SkillStore
    ) -> None:
        store.save(make_skill())
        catalog = store.catalog()
        assert catalog[0].pop("last_activity") > 0
        assert catalog == [
            {
                "slug": "open-notes",
                "name": "Open Notes",
                "description": "Opens the notes app.",
                "version": 1,
                "source": "dictated",
                "state": "draft",
                "pinned": False,
                "archived": False,
                "use_count": 0,
                "failure_count": 0,
                "success_count": 0,
            }
        ]

    def test_phrases_match_exactly_and_ambiguity_does_not_choose_arbitrarily(
        self, store: SkillStore
    ) -> None:
        store.save(replace(make_skill(), phrases=("show my notes",)))
        assert store.find_by_phrase(" SHOW  MY NOTES ").name == "Open Notes"
        assert store.find_by_phrase("show my") is None
        store.save(replace(make_skill("Other Notes"), phrases=("show my notes",)))
        assert store.find_by_phrase("show my notes") is None

    def test_hand_edits_revert_enabled_skills_to_draft_and_preserve_raw_text(
        self, store: SkillStore, tmp_path: Path
    ) -> None:
        store.save(replace(make_skill(), state="enabled"))
        path = tmp_path / "open-notes.md"
        changed = path.read_text(encoding="utf-8").replace(
            "Open the notes app", "Open my notes"
        )
        path.write_text(changed, encoding="utf-8")
        assert store.get("Open Notes").state == "draft"
        assert store.original_text("Open Notes", 2) == changed
        assert store.version("Open Notes", 1).state == "enabled"

    def test_restoring_enabled_version_creates_a_new_draft(
        self, store: SkillStore
    ) -> None:
        store.save(replace(make_skill(), state="enabled"))
        store.archive("Open Notes")
        restored = store.restore("Open Notes")
        assert (restored.state, restored.version) == ("draft", 2)
        assert store.version("Open Notes", 1).state == "enabled"

    def test_undo_recovers_a_broken_hand_edit_from_the_only_valid_version(
        self, store: SkillStore, tmp_path: Path
    ) -> None:
        store.save(make_skill())
        path = tmp_path / "open-notes.md"
        path.write_text("broken edit", encoding="utf-8")
        assert store.get("Open Notes") is None
        restored = store.undo("Open Notes")
        assert restored.version == 2
        assert store.get("Open Notes").steps[0].purpose == "Open the notes app"


class TestOutcomeAccounting:
    def test_distinct_terminal_runs_count_once_and_survive_restart(
        self, store: SkillStore, tmp_path: Path
    ) -> None:
        store.save(make_skill())
        assert store.record_outcome(
            "open-notes", "done-1", "done", at=3_000_000_000_000
        )
        assert not store.record_outcome(
            "Open Notes", "done-1", "error", at=3_000_000_000_050
        )
        for index, outcome in enumerate(("error", "uncertain", "cancelled")):
            assert store.record_outcome(
                "Open Notes", f"failure-{index}", outcome, at=3_000_000_000_001 + index
            )
        stats = SkillStore(tmp_path).catalog()[0]
        assert (stats["use_count"], stats["success_count"], stats["failure_count"]) == (
            4,
            1,
            3,
        )
        assert stats["last_activity"] == 3_000_000_000_003
        assert store.versions("Open Notes") == [1]

    def test_loading_and_pinning_do_not_count_as_executions(
        self, store: SkillStore
    ) -> None:
        store.save(make_skill())
        store.get("Open Notes")
        store.set_pinned("Open Notes", True)
        assert store.catalog()[0]["use_count"] == 0

    def test_accounting_rejects_nonterminal_states_without_changing_counts(
        self, store: SkillStore
    ) -> None:
        store.save(make_skill())
        with pytest.raises(ValueError):
            store.record_outcome("Open Notes", "running-1", "running")
        assert store.catalog()[0]["use_count"] == 0

    def test_duplicate_deliveries_from_concurrent_instances_count_once(
        self, store: SkillStore, tmp_path: Path
    ) -> None:
        store.save(make_skill())
        stores = [SkillStore(tmp_path) for _ in range(8)]
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(
                executor.map(
                    lambda other: other.record_outcome(
                        "Open Notes", "same-run", "done"
                    ),
                    stores,
                )
            )
        assert sum(results) == 1
        assert store.catalog()[0]["success_count"] == 1


class TestSafetyAndConcurrency:
    def test_failed_atomic_replacement_preserves_the_previous_file_and_version(
        self, store: SkillStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store.save(make_skill())
        original = (tmp_path / "open-notes.md").read_bytes()

        def refuse_replace(source, destination):
            raise PermissionError("replacement denied")

        monkeypatch.setattr(skill_store.os, "replace", refuse_replace)
        with pytest.raises(PermissionError):
            store.save(make_skill(purpose="Changed"))
        assert (tmp_path / "open-notes.md").read_bytes() == original
        assert store.versions("Open Notes") == [1]
        assert not list(tmp_path.glob("*.tmp"))

    @pytest.mark.parametrize(
        "name", ["../outside", "..\\outside", "C:\\outside", ".", "..", "CON", "aux"]
    )
    def test_rejects_unsafe_names_without_writing_a_skill(
        self, store: SkillStore, tmp_path: Path, name: str
    ) -> None:
        with pytest.raises(ValueError):
            store.save(make_skill(name))
        assert not list(tmp_path.glob("*.md"))

    def test_does_not_merge_names_that_slug_to_the_same_filename(
        self, store: SkillStore
    ) -> None:
        store.save(make_skill())
        with pytest.raises(ValueError, match="collision"):
            store.save(make_skill("Open-Notes"))
        assert store.get("Open Notes").name == "Open Notes"
        assert store.versions("Open Notes") == [1]

    def test_concurrent_instances_preserve_every_version(
        self, store: SkillStore, tmp_path: Path
    ) -> None:
        store.save(make_skill())
        stores = [SkillStore(tmp_path) for _ in range(12)]

        def edit(index: int) -> int:
            return stores[index].save(make_skill(purpose=f"Notes edit {index}")).version

        with ThreadPoolExecutor(max_workers=6) as executor:
            versions = list(executor.map(edit, range(12)))
        assert sorted(versions) == list(range(2, 14))
        reopened = SkillStore(tmp_path)
        assert reopened.versions("Open Notes") == list(range(1, 14))
        purposes = {
            reopened.version("Open Notes", number).steps[0].purpose
            for number in range(2, 14)
        }
        assert purposes == {f"Notes edit {index}" for index in range(12)}
        assert reopened.get("Open Notes").version == 13

    def test_handmade_original_is_recorded_without_rewriting(
        self, store: SkillStore, tmp_path: Path
    ) -> None:
        path = tmp_path / "custom.md"
        original = render_skill(make_skill()) + "\n"
        path.write_text(original, encoding="utf-8")
        assert store.get("Open Notes").version == 1
        assert path.read_text(encoding="utf-8") == original
        assert store.version_text("custom", 1) == original

    def test_symlinked_skill_cannot_read_or_overwrite_outside_root(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "library"
        root.mkdir()
        outside = tmp_path / "outside.md"
        original = render_skill(make_skill())
        outside.write_text(original, encoding="utf-8")
        link = root / "open-notes.md"
        try:
            link.symlink_to(outside)
        except OSError:
            pytest.skip("Host does not permit creating symlinks")
        store = SkillStore(root)
        assert store.get("open-notes") is None
        with pytest.raises(ValueError):
            store.save(make_skill())
        assert outside.read_text(encoding="utf-8") == original
