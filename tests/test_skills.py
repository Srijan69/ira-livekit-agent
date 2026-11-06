"""Tests for the Hermes-style skills engine (src/skills.py).

Covers:
- Frontmatter parsing (YAML metadata, platform filters, requirements)
- Skill discovery and catalog indexing
- Platform-aware filtering (windows, linux, darwin)
- Progressive disclosure (list_skills, view_skill, supporting files)
- Skill management (create, edit, patch, delete, write_file, remove_file)
- Security / path traversal defenses
- Integration with Assistant agent tools (skills_list, skill_view, skill_manage)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent import Assistant
from skills import (
    SkillCatalog,
    parse_frontmatter,
)


def test_parse_frontmatter_valid():
    text = """---
name: spotify-player
description: Plays tracks and playlists on Spotify
category: media
version: 1.2.0
platform: windows
requires:
  - selenium
  - chrome
metadata:
  author: boss
---
# Instructions
Step 1: Open Spotify
Step 2: Hit play
"""
    meta, body = parse_frontmatter(text)
    assert meta["name"] == "spotify-player"
    assert meta["description"] == "Plays tracks and playlists on Spotify"
    assert meta["category"] == "media"
    assert meta["version"] == "1.2.0"
    assert meta["platform"] == "windows"
    assert "selenium" in meta["requires"]
    assert meta["metadata"]["author"] == "boss"
    assert "Step 1: Open Spotify" in body


def test_parse_frontmatter_without_frontmatter():
    text = "# Just markdown\nNo frontmatter here."
    meta, body = parse_frontmatter(text)
    assert meta == {}
    assert "Just markdown" in body


def test_parse_frontmatter_malformed_yaml():
    text = """---
name: broken
category: [unclosed list
---
Body text
"""
    meta, body = parse_frontmatter(text)
    assert meta == {}
    assert "Body text" in body


def test_skill_catalog_discovery_and_nesting(tmp_path: Path):
    root = tmp_path / "skills"
    # Skill in subcategory folder
    media_skill = root / "media" / "play-song"
    media_skill.mkdir(parents=True)
    (media_skill / "SKILL.md").write_text(
        """---
name: play-song
description: Play music
category: media
---
Play instructions.
""",
        encoding="utf-8",
    )

    # Skill in root folder
    general_skill = root / "notepad-write"
    general_skill.mkdir(parents=True)
    (general_skill / "SKILL.md").write_text(
        """---
name: notepad-write
description: Write text in notepad
category: desktop
---
Notepad instructions.
""",
        encoding="utf-8",
    )

    catalog = SkillCatalog(root)
    assert len(catalog.list_skills()) == 2
    assert catalog.get_skill("play-song") is not None
    assert catalog.get_skill("notepad-write") is not None
    assert len(catalog.list_skills(category="media")) == 1
    assert catalog.list_skills(category="media")[0].name == "play-song"


def test_skill_catalog_platform_filtering(tmp_path: Path):
    root = tmp_path / "skills"
    # Linux-only skill
    linux_dir = root / "linux-tool"
    linux_dir.mkdir(parents=True)
    (linux_dir / "SKILL.md").write_text(
        """---
name: linux-tool
description: Linux command
platform: linux
---
Run bash.
""",
        encoding="utf-8",
    )

    # Universal skill
    univ_dir = root / "univ-tool"
    univ_dir.mkdir(parents=True)
    (univ_dir / "SKILL.md").write_text(
        """---
name: univ-tool
description: Cross platform
---
Run everywhere.
""",
        encoding="utf-8",
    )

    # Windows catalog should omit linux-tool
    catalog = SkillCatalog(root, platform_override="windows")
    names = [s.name for s in catalog.list_skills()]
    assert "univ-tool" in names
    assert "linux-tool" not in names

    # Linux catalog should include linux-tool
    catalog_linux = SkillCatalog(root, platform_override="linux")
    names_linux = [s.name for s in catalog_linux.list_skills()]
    assert "linux-tool" in names_linux
    assert "univ-tool" in names_linux


def test_skill_catalog_prompt_generation(tmp_path: Path):
    root = tmp_path / "skills"
    sk_dir = root / "test-skill"
    sk_dir.mkdir(parents=True)
    (sk_dir / "SKILL.md").write_text(
        """---
name: test-skill
description: Example description
category: testing
---
Instructions here.
""",
        encoding="utf-8",
    )

    catalog = SkillCatalog(root)
    prompt = catalog.render_prompt_catalog()
    assert "<available_skills>" in prompt
    assert "- test-skill (testing): Example description" in prompt
    assert "</available_skills>" in prompt


def test_skill_view_instructions_and_linked_files(tmp_path: Path):
    root = tmp_path / "skills"
    sk_dir = root / "complex-skill"
    ref_dir = sk_dir / "references"
    ref_dir.mkdir(parents=True)
    (sk_dir / "SKILL.md").write_text(
        """---
name: complex-skill
description: Multi-file skill
category: testing
---
Main skill instructions.
""",
        encoding="utf-8",
    )
    (ref_dir / "guide.md").write_text("# Deep Guide\nDetailed steps.", encoding="utf-8")

    catalog = SkillCatalog(root)
    # View main skill
    view_res = catalog.view_skill("complex-skill")
    assert view_res["success"] is True
    assert "Main skill instructions" in view_res["instructions"]
    assert "references/guide.md" in view_res["linked_files"].get("references", [])

    # View sub-file
    file_res = catalog.view_skill("complex-skill", file_path="references/guide.md")
    assert file_res["success"] is True
    assert "Deep Guide" in file_res["content"]


def test_skill_view_path_traversal_protection(tmp_path: Path):
    root = tmp_path / "skills"
    sk_dir = root / "safe-skill"
    sk_dir.mkdir(parents=True)
    (sk_dir / "SKILL.md").write_text(
        """---
name: safe-skill
description: Safe skill
---
Safe instructions.
""",
        encoding="utf-8",
    )

    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("SUPER_SECRET", encoding="utf-8")

    catalog = SkillCatalog(root)
    # Attempt directory traversal
    res = catalog.view_skill("safe-skill", file_path="../../secret.txt")
    assert res["success"] is False
    assert "Access denied" in res["error"]


def test_skill_management_lifecycle(tmp_path: Path):
    root = tmp_path / "skills"
    catalog = SkillCatalog(root)

    # 1. Create skill
    create_res = catalog.create_skill(
        name="backup-job",
        content="# Backup\nRun nightly backup.",
        category="system",
        description="Backs up system state",
    )
    assert create_res["success"] is True
    assert catalog.get_skill("backup-job") is not None

    # 2. Patch skill
    patch_res = catalog.patch_skill(
        name="backup-job",
        old_string="Run nightly backup.",
        new_string="Run hourly backup.",
    )
    assert patch_res["success"] is True
    view = catalog.view_skill("backup-job")
    assert "Run hourly backup." in view["instructions"]

    # 3. Write supporting file
    write_res = catalog.write_supporting_file(
        name="backup-job",
        file_path="scripts/backup.ps1",
        content="Write-Output 'Backing up'",
    )
    assert write_res["success"] is True
    file_view = catalog.view_skill("backup-job", file_path="scripts/backup.ps1")
    assert file_view["success"] is True
    assert "Backing up" in file_view["content"]

    # 4. Patch supporting file
    patch_file_res = catalog.patch_skill(
        name="backup-job",
        old_string="Backing up",
        new_string="Backup Complete",
        file_path="scripts/backup.ps1",
    )
    assert patch_file_res["success"] is True
    patched_file_view = catalog.view_skill("backup-job", file_path="scripts/backup.ps1")
    assert "Backup Complete" in patched_file_view["content"]

    # 5. Remove supporting file
    rem_file_res = catalog.remove_supporting_file(
        name="backup-job", file_path="scripts/backup.ps1"
    )
    assert rem_file_res["success"] is True

    # 6. Delete skill
    del_res = catalog.delete_skill("backup-job")
    assert del_res["success"] is True
    assert catalog.get_skill("backup-job") is None


@pytest.mark.asyncio
async def test_assistant_skills_tools_integration(tmp_path: Path):
    root = tmp_path / "skills"
    catalog = SkillCatalog(root)
    agent = Assistant(skills_catalog=catalog)

    # Create via agent tool
    create_out = await agent.skill_manage(
        None,
        action="create",
        name="morning-briefing",
        content="# Morning Briefing\nGive weather and calendar.",
        category="productivity",
    )
    assert "Created" in create_out

    # List via agent tool
    list_out = await agent.skills_list(None)
    assert "morning-briefing" in list_out

    # View via agent tool
    view_out = await agent.skill_view(None, name="morning-briefing")
    assert "weather and calendar" in view_out

    # Patch via agent tool
    patch_out = await agent.skill_manage(
        None,
        action="patch",
        name="morning-briefing",
        old_string="Give weather and calendar.",
        new_string="Give weather, calendar, and email digest.",
    )
    assert "Patched" in patch_out

    view_updated = await agent.skill_view(None, name="morning-briefing")
    assert "email digest" in view_updated

    # Delete via agent tool
    del_out = await agent.skill_manage(None, action="delete", name="morning-briefing")
    assert "Deleted" in del_out
