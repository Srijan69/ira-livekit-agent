"""Hermes-compatible Skills Engine for IRA.

Port of the Hermes Agent skill system (progressive disclosure):
- Skills live in `skills/<category>/<skill-name>/` (or `skills/<skill-name>/`).
- Each skill package contains a `SKILL.md` with YAML frontmatter + markdown instructions.
- Optional companion folders: `references/`, `templates/`, `assets/`, `scripts/`.
- Progressive disclosure pattern:
  - System prompt receives a compact catalog of name + category + description.
  - `skills_list(category)` lists available skills.
  - `skill_view(name, file_path)` reads the full SKILL.md or supporting reference/script file.
  - `skill_manage(action, name, ...)` handles full skill authoring (create, edit, patch, delete, write_file, remove_file).
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("agent.skills")

# Supporting subfolder names
SUPPORT_DIRS = ("references", "templates", "assets", "scripts")

DETERMINISTIC_TOOLS = {
    "open_app",
    "browse_to",
    "open_spotify",
    "read_page",
    "list_page_elements",
    "click_element",
    "type_into_element",
    "go_back",
    "close_browser",
    "spotify_control",
    "spotify_play_playlist",
    "new_tab",
    "switch_tab",
    "list_tabs",
    "close_tab",
    "select_dropdown_option",
}


def current_platform() -> str:
    """Normalize system platform to hermes convention (windows, macos, linux)."""
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def default_skills_dir() -> Path:
    """Directory where skills are stored."""
    override = os.environ.get("IRA_SKILLS_DIR")
    if override:
        return Path(override).resolve()
    return (Path(__file__).resolve().parent.parent / "skills").resolve()


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter delimited by `---` lines using PyYAML with safe fallback."""
    text = content.strip()
    if not text.startswith("---"):
        return {}, text

    lines = text.splitlines()
    end_idx = -1
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end_idx = idx
            break

    if end_idx == -1:
        return {}, text

    frontmatter_raw = "\n".join(lines[1:end_idx])
    body = "\n".join(lines[end_idx + 1 :]).strip()

    try:
        import yaml

        parsed = yaml.safe_load(frontmatter_raw)
        metadata = parsed if isinstance(parsed, dict) else {}
    except Exception as exc:
        logger.debug("PyYAML safe_load failed (%s), falling back to simple parser", exc)
        metadata = {}

    return metadata, body


@dataclass(frozen=True)
class SkillInfo:
    name: str
    description: str
    category: str
    version: str = "1.0.0"
    author: str = "IRA Agent"
    platforms: tuple[str, ...] = ("windows", "macos", "linux")
    tags: tuple[str, ...] = ()
    path: Path | None = None
    body: str = ""
    linked_files: dict[str, list[str]] = field(default_factory=dict)


class SkillCatalog:
    """Manages discovery, inspection, and authoring of Hermes-style skills."""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        platform_override: str | None = None,
    ) -> None:
        self.root = Path(root).resolve() if root is not None else default_skills_dir()
        self.platform_override = platform_override
        self.root.mkdir(parents=True, exist_ok=True)

    def _discover_linked_files(self, skill_dir: Path) -> dict[str, list[str]]:
        """Collect relative paths to files in references/, templates/, assets/, scripts/."""
        files: dict[str, list[str]] = {}
        for sub in SUPPORT_DIRS:
            sub_path = skill_dir / sub
            if sub_path.is_dir():
                found = [
                    f.relative_to(skill_dir).as_posix()
                    for f in sub_path.rglob("*")
                    if f.is_file()
                ]
                if found:
                    files[sub] = sorted(found)
        return files

    def _parse_skill_file(self, skill_md: Path) -> SkillInfo | None:
        """Parse a single SKILL.md file into SkillInfo."""
        try:
            content = skill_md.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning("Could not read skill file %s: %exc", skill_md, exc)
            return None

        frontmatter, body = parse_frontmatter(content)
        skill_dir = skill_md.parent

        # Derive category from folder structure if not in frontmatter
        category = "general"
        hermes_meta = frontmatter.get("metadata", {})
        if isinstance(hermes_meta, dict) and "hermes" in hermes_meta:
            hermes_inner = hermes_meta.get("hermes", {})
            if isinstance(hermes_inner, dict) and hermes_inner.get("category"):
                category = hermes_inner["category"]
        elif frontmatter.get("category"):
            category = frontmatter["category"]
        else:
            rel = skill_dir.relative_to(self.root)
            if len(rel.parts) > 1:
                category = rel.parts[0]

        name = frontmatter.get("name") or skill_dir.name
        description = frontmatter.get("description", "")
        if not description:
            # Fall back to first non-heading body line
            for line in body.splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    description = stripped[:120]
                    break

        platforms = (
            frontmatter.get("platforms")
            or frontmatter.get("platform")
            or ["windows", "macos", "linux"]
        )
        if isinstance(platforms, str):
            platforms = [platforms]

        tags = frontmatter.get("tags", [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]

        linked_files = self._discover_linked_files(skill_dir)

        return SkillInfo(
            name=name,
            description=description,
            category=category,
            version=str(frontmatter.get("version", "1.0.0")),
            author=str(frontmatter.get("author", "IRA Agent")),
            platforms=tuple(str(p).lower() for p in platforms),
            tags=tuple(str(t).lower() for t in tags),
            path=skill_md,
            body=body,
            linked_files=linked_files,
        )

    def list_skills(self, category: str | None = None) -> list[SkillInfo]:
        """Scan skills directory and return loadable skills compatible with current platform."""
        skills: list[SkillInfo] = []
        plat = self.platform_override or current_platform()

        if not self.root.exists():
            return skills

        # Search for all SKILL.md files
        for skill_md in self.root.rglob("SKILL.md"):
            info = self._parse_skill_file(skill_md)
            if info is None:
                continue
            # Platform gating
            if plat not in info.platforms and "all" not in info.platforms:
                continue
            # Category filter
            if category and info.category.lower() != category.lower():
                continue
            skills.append(info)

        skills.sort(key=lambda s: (s.category, s.name))
        return skills

    def get_skill(self, name: str) -> SkillInfo | None:
        """Find a skill by name or slug."""
        target = name.strip().lower()
        for s in self.list_skills():
            if s.name.lower() == target:
                return s

        # Try matching direct folder relative to root
        direct = self.root / name / "SKILL.md"
        if direct.exists():
            return self._parse_skill_file(direct)

        # Tolerant search: substring match if unambiguous
        matches = [s for s in self.list_skills() if target in s.name.lower()]
        if len(matches) == 1:
            return matches[0]

        return None

    def view_skill(self, name: str, file_path: str | None = None) -> dict[str, Any]:
        """Progressive disclosure view: return full skill doc or linked supporting file."""
        skill = self.get_skill(name)
        if skill is None or skill.path is None:
            return {
                "success": False,
                "error": f"Skill '{name}' not found. Use skills_list to see available skills.",
            }

        skill_dir = skill.path.parent

        if file_path:
            clean_rel = Path(file_path.strip().replace("\\", "/"))
            target_file = (skill_dir / clean_rel).resolve()
            # Path traversal safety check
            if not target_file.is_relative_to(skill_dir):
                return {
                    "success": False,
                    "error": "Access denied: file path traverses outside skill directory.",
                }
            if not target_file.exists() or not target_file.is_file():
                return {
                    "success": False,
                    "error": f"File '{file_path}' not found in skill '{skill.name}'.",
                }

            try:
                content = target_file.read_text(encoding="utf-8")
                return {
                    "success": True,
                    "skill": skill.name,
                    "file_path": file_path,
                    "content": content,
                }
            except Exception as exc:
                return {
                    "success": False,
                    "error": f"Could not read file '{file_path}': {exc}",
                }

        return {
            "success": True,
            "name": skill.name,
            "category": skill.category,
            "version": skill.version,
            "author": skill.author,
            "description": skill.description,
            "platforms": list(skill.platforms),
            "tags": list(skill.tags),
            "linked_files": skill.linked_files,
            "instructions": skill.body,
        }

    def catalog(self, include_archived: bool = False) -> list[dict[str, Any]]:
        """Return catalog list compatible with legacy callers, curators, and diagnostics."""
        return [
            {
                "name": s.name,
                "slug": s.name,
                "description": s.description,
                "category": s.category,
                "version": s.version,
                "path": str(s.path) if s.path else "",
                "pinned": False,
                "last_activity": 0,
                "failure_count": 0,
                "success_count": 0,
            }
            for s in self.list_skills()
        ]

    def render_prompt_catalog(self) -> str:
        """Render a compact catalog for injection into the system prompt."""
        skills = self.list_skills()
        if not skills:
            return ""

        lines = [f"- {s.name} ({s.category}): {s.description}" for s in skills]
        return "\n<available_skills>\n" + "\n".join(lines) + "\n</available_skills>\n"

    # --- Authoring & Management ---

    def create_skill(
        self,
        name: str,
        content: str,
        category: str = "general",
        description: str = "",
    ) -> dict[str, Any]:
        """Create a new skill package under skills/<category>/<name>/SKILL.md."""
        clean_name = re.sub(r"[^a-z0-9_-]+", "-", name.strip().lower()).strip("-")
        clean_cat = (
            re.sub(r"[^a-z0-9_-]+", "-", category.strip().lower()).strip("-")
            or "general"
        )

        if not clean_name:
            return {"success": False, "error": "Invalid skill name."}

        target_dir = self.root / clean_cat / clean_name
        target_dir.mkdir(parents=True, exist_ok=True)
        skill_md = target_dir / "SKILL.md"

        # If content doesn't have frontmatter, add default header
        text = content.strip()
        if not text.startswith("---"):
            desc = description.strip() or f"Custom skill for {clean_name}."
            header = (
                f'---\nname: {clean_name}\ndescription: "{desc}"\n'
                f'version: 1.0.0\nauthor: "IRA Agent"\nplatforms: [windows, macos, linux]\n'
                f"metadata:\n  hermes:\n    category: {clean_cat}\n---\n\n"
            )
            text = header + text

        skill_md.write_text(text, encoding="utf-8")
        return {
            "success": True,
            "message": f"Created skill '{clean_name}' in category '{clean_cat}'.",
            "path": str(skill_md),
        }

    def edit_skill(self, name: str, content: str) -> dict[str, Any]:
        """Overwrite SKILL.md of an existing skill."""
        skill = self.get_skill(name)
        if skill is None or skill.path is None:
            return {"success": False, "error": f"Skill '{name}' not found."}

        skill.path.write_text(content.strip(), encoding="utf-8")
        return {"success": True, "message": f"Updated skill '{skill.name}'."}

    def patch_skill(
        self,
        name: str,
        old_string: str,
        new_string: str,
        file_path: str | None = None,
    ) -> dict[str, Any]:
        """Patch a substring within SKILL.md or a supporting file."""
        skill = self.get_skill(name)
        if skill is None or skill.path is None:
            return {"success": False, "error": f"Skill '{name}' not found."}

        target = skill.path
        if file_path:
            target = (skill.path.parent / file_path).resolve()
            if not target.is_relative_to(skill.path.parent) or not target.exists():
                return {
                    "success": False,
                    "error": f"File '{file_path}' not found in skill.",
                }

        content = target.read_text(encoding="utf-8")
        if old_string not in content:
            return {
                "success": False,
                "error": f"old_string '{old_string}' not found in {target.name}.",
            }
        if content.count(old_string) > 1:
            return {
                "success": False,
                "error": f"old_string '{old_string}' appears multiple times. Include more context.",
            }

        patched = content.replace(old_string, new_string, 1)
        target.write_text(patched, encoding="utf-8")
        return {
            "success": True,
            "message": f"Patched {target.name} in skill '{skill.name}'.",
        }

    def delete_skill(self, name: str) -> dict[str, Any]:
        """Delete a skill package directory."""
        skill = self.get_skill(name)
        if skill is None or skill.path is None:
            return {"success": False, "error": f"Skill '{name}' not found."}

        skill_dir = skill.path.parent
        try:
            shutil.rmtree(skill_dir)
            # Remove empty parent category directory if applicable
            parent = skill_dir.parent
            if parent != self.root and parent.is_dir() and not any(parent.iterdir()):
                with contextlib.suppress(OSError):
                    parent.rmdir()
            return {"success": True, "message": f"Deleted skill '{skill.name}'."}
        except Exception as exc:
            return {"success": False, "error": f"Failed to delete skill: {exc}"}

    def write_supporting_file(
        self,
        name: str,
        file_path: str,
        content: str = "",
        file_content: str = "",
    ) -> dict[str, Any]:
        """Create or overwrite a supporting file in references/, templates/, assets/, or scripts/."""
        text = content or file_content
        skill = self.get_skill(name)
        if skill is None or skill.path is None:
            return {"success": False, "error": f"Skill '{name}' not found."}

        skill_dir = skill.path.parent
        clean_rel = Path(file_path.strip().replace("\\", "/"))
        first_part = clean_rel.parts[0] if clean_rel.parts else ""
        if first_part not in SUPPORT_DIRS:
            return {
                "success": False,
                "error": f"Supporting files must live in one of: {', '.join(SUPPORT_DIRS)}.",
            }

        target = (skill_dir / clean_rel).resolve()
        if not target.is_relative_to(skill_dir):
            return {
                "success": False,
                "error": "Access denied: path traverses outside skill directory.",
            }

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return {
            "success": True,
            "message": f"Saved {file_path} in skill '{skill.name}'.",
        }

    def remove_supporting_file(self, name: str, file_path: str) -> dict[str, Any]:
        """Remove a supporting file from a skill package."""
        skill = self.get_skill(name)
        if skill is None or skill.path is None:
            return {"success": False, "error": f"Skill '{name}' not found."}

        skill_dir = skill.path.parent
        target = (skill_dir / file_path.strip().replace("\\", "/")).resolve()
        if not target.is_relative_to(skill_dir) or not target.exists():
            return {"success": False, "error": f"File '{file_path}' not found."}

        target.unlink()
        return {
            "success": True,
            "message": f"Removed {file_path} from skill '{skill.name}'.",
        }
