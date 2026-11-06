"""Human-editable skills with immutable history and recoverable archival.

Markdown is the visible working copy. SQLite retains every accepted version and
its exact original text. A write transaction also serializes filesystem changes
across store instances and processes; individual file replacements are atomic.
Reading notices hand edits, while malformed edits stay visible as issues and
never replace the last usable version.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import tempfile
import time
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

from skill_format import (
    Issue,
    Skill,
    SkillFormatError,
    convert_legacy,
    is_legacy,
    parse_skill,
    render_skill,
)

_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def skill_slug(name: str) -> str:
    """Return a portable filename, rejecting paths and Windows device names."""
    if (
        not isinstance(name, str)
        or not name.strip()
        or re.search(r"[\\/:\x00-\x1f]", name)
    ):
        raise ValueError("skill name must be a name, not a path")
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    if not slug or slug in _RESERVED_NAMES:
        raise ValueError("skill name cannot be empty or a reserved filename")
    return slug


@dataclass(frozen=True)
class StoredSkill:
    slug: str
    skill: Skill | None
    issues: tuple[Issue, ...] = ()
    archived: bool = False
    use_count: int = 0
    failure_count: int = 0
    success_count: int = 0
    last_activity: int = 0


class SkillStore:
    def __init__(
        self, root: str | Path | None = None, *, known_tools: set[str] | None = None
    ) -> None:
        self.root = (
            Path(root)
            if root is not None
            else Path(__file__).resolve().parent.parent / "workflows"
        ).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.known_tools = set(known_tools) if known_tools is not None else None
        self._db_path = self.root / ".skills.sqlite3"
        with self._transaction() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS skills (
                slug TEXT PRIMARY KEY, name TEXT NOT NULL, last_text TEXT NOT NULL,
                archived INTEGER NOT NULL DEFAULT 0,
                use_count INTEGER NOT NULL DEFAULT 0,
                failure_count INTEGER NOT NULL DEFAULT 0,
                success_count INTEGER NOT NULL DEFAULT 0,
                last_activity INTEGER NOT NULL DEFAULT 0
            )""")
            columns = {row["name"] for row in db.execute("PRAGMA table_info(skills)")}
            for column in ("success_count", "last_activity"):
                if column not in columns:
                    db.execute(
                        f"ALTER TABLE skills ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0"
                    )
            db.execute(
                "UPDATE skills SET last_activity=? WHERE last_activity=0",
                (time.time_ns() // 1_000_000,),
            )
            db.execute("""CREATE TABLE IF NOT EXISTS versions (
                slug TEXT NOT NULL, version INTEGER NOT NULL,
                text TEXT NOT NULL, original_text TEXT NOT NULL,
                PRIMARY KEY (slug, version)
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS rejected_edits (
                slug TEXT NOT NULL, digest TEXT NOT NULL, text TEXT NOT NULL,
                PRIMARY KEY (slug, digest)
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS outcomes (
                run_id TEXT PRIMARY KEY, slug TEXT NOT NULL,
                outcome TEXT NOT NULL, at INTEGER NOT NULL
            )""")

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        if self._db_path.is_symlink() or self._db_path.resolve().parent != self.root:
            raise ValueError("skill history must remain inside its library")
        db = sqlite3.connect(self._db_path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _path(self, slug: str) -> Path:
        # A hand-made filename need not equal its skill's slug, but it must
        # still be one direct child of the library, with no symlink traversal.
        skill_slug(slug)
        path = self.root / f"{slug}.md"
        if path.is_symlink() or path.resolve().parent != self.root:
            raise ValueError("skill path must remain inside its library")
        return path

    def _write(self, slug: str, text: str) -> None:
        path = self._path(slug)
        descriptor, temporary = tempfile.mkstemp(
            prefix=".skill-", suffix=".tmp", dir=self.root
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as output:
                output.write(text)
                output.flush()
                os.fsync(output.fileno())
            self._path(slug)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _parse(self, text: str) -> Skill:
        if is_legacy(text):
            skill = convert_legacy(text)
            # Conversion proposes the next version; a read itself does not
            # migrate or rewrite an old file.
            skill = replace(skill, version=max(1, skill.version - 1))
            return parse_skill(render_skill(skill), known_tools=self.known_tools)
        return parse_skill(text, known_tools=self.known_tools)

    @staticmethod
    def _next_version(db: sqlite3.Connection, slug: str) -> int:
        row = db.execute(
            "SELECT MAX(version) AS latest FROM versions WHERE slug=?", (slug,)
        ).fetchone()
        return (row["latest"] or 0) + 1

    @staticmethod
    def _record(
        db: sqlite3.Connection,
        slug: str,
        skill: Skill,
        text: str,
        original: str,
        *,
        archived: bool = False,
    ) -> None:
        db.execute(
            "INSERT INTO versions (slug,version,text,original_text) VALUES (?,?,?,?)",
            (slug, skill.version, text, original),
        )
        db.execute(
            """INSERT INTO skills (slug,name,last_text,archived,last_activity) VALUES (?,?,?,?,?)
            ON CONFLICT(slug) DO UPDATE SET name=excluded.name,
            last_text=excluded.last_text, archived=excluded.archived,
            last_activity=MAX(skills.last_activity,excluded.last_activity)""",
            (slug, skill.name, text, int(archived), time.time_ns() // 1_000_000),
        )

    def _save(
        self,
        db: sqlite3.Connection,
        slug: str,
        skill: Skill,
        *,
        source: str | None = None,
        original: str | None = None,
        archived: bool = False,
    ) -> Skill:
        saved = replace(
            skill, version=self._next_version(db, slug), source=source or skill.source
        )
        text = render_skill(saved)
        parsed = parse_skill(text, known_tools=self.known_tools)
        if parsed != saved:
            raise ValueError("skill cannot be saved without losing information")
        if not archived:
            self._write(slug, text)
        self._record(
            db,
            slug,
            saved,
            text,
            original if original is not None else text,
            archived=archived,
        )
        return saved

    def _refresh(self, db: sqlite3.Connection) -> list[StoredSkill]:
        entries: list[StoredSkill] = []
        seen: set[str] = set()
        for path in sorted(self.root.glob("*.md")):
            slug = path.stem
            seen.add(slug)
            row = db.execute("SELECT * FROM skills WHERE slug=?", (slug,)).fetchone()
            text = None
            try:
                safe_path = self._path(slug)
                with safe_path.open(encoding="utf-8", newline="") as source:
                    text = source.read()
                skill = self._parse(text)
                skill_slug(skill.name)
                if row is None:
                    self._record(db, slug, skill, text, text)
                elif row["last_text"] != text:
                    skill = self._save(
                        db,
                        slug,
                        replace(skill, state="draft"),
                        source="edited",
                        original=text,
                    )
                elif row["archived"]:
                    db.execute("UPDATE skills SET archived=0 WHERE slug=?", (slug,))
                current_row = db.execute(
                    "SELECT * FROM skills WHERE slug=?", (slug,)
                ).fetchone()
                entries.append(
                    StoredSkill(
                        slug,
                        skill,
                        use_count=current_row["use_count"],
                        failure_count=current_row["failure_count"],
                        success_count=current_row["success_count"],
                        last_activity=current_row["last_activity"],
                    )
                )
            except (ValueError, OSError) as exc:
                issues = (
                    exc.issues
                    if isinstance(exc, SkillFormatError)
                    else (Issue(0, str(exc)),)
                )
                if text is not None:
                    digest = hashlib.sha256(text.encode()).hexdigest()
                    db.execute(
                        "INSERT OR IGNORE INTO rejected_edits (slug,digest,text) VALUES (?,?,?)",
                        (slug, digest, text),
                    )
                entries.append(StoredSkill(slug, None, issues))
        # A hand-deleted file remains recoverable just like an explicit archive.
        for row in db.execute("SELECT * FROM skills ORDER BY slug").fetchall():
            if row["slug"] in seen:
                continue
            if not row["archived"]:
                db.execute("UPDATE skills SET archived=1 WHERE slug=?", (row["slug"],))
            try:
                skill = self._parse(row["last_text"])
                entries.append(
                    StoredSkill(
                        row["slug"],
                        skill,
                        archived=True,
                        use_count=row["use_count"],
                        failure_count=row["failure_count"],
                        success_count=row["success_count"],
                        last_activity=row["last_activity"],
                    )
                )
            except SkillFormatError as exc:
                entries.append(
                    StoredSkill(row["slug"], None, exc.issues, archived=True)
                )
        return sorted(entries, key=lambda entry: entry.slug)

    @staticmethod
    def _find_row(db: sqlite3.Connection, name: str) -> sqlite3.Row | None:
        skill_slug(name)
        normalized = name.strip().casefold()
        rows = db.execute("SELECT * FROM skills").fetchall()
        matches = [
            row
            for row in rows
            if row["name"].casefold() == normalized
            or row["slug"].casefold() == normalized
        ]
        return matches[0] if len(matches) == 1 else None

    def save(self, skill: Skill, *, source: str | None = None) -> Skill:
        slug = skill_slug(skill.name)
        proposed = replace(skill, source=source or skill.source)
        if (
            parse_skill(render_skill(proposed), known_tools=self.known_tools)
            != proposed
        ):
            raise ValueError("skill cannot be saved without losing information")
        with self._transaction() as db:
            self._refresh(db)
            row = self._find_row(db, skill.name)
            if row is not None:
                slug = row["slug"]
            collision = db.execute(
                "SELECT name FROM skills WHERE slug=?", (slug,)
            ).fetchone()
            if (
                collision is not None
                and collision["name"].casefold() != skill.name.casefold()
            ):
                raise ValueError(f"skill filename collision: {slug}")
            return self._save(db, slug, proposed)

    def list(self, *, include_archived: bool = False) -> list[StoredSkill]:
        with self._transaction() as db:
            return [
                entry
                for entry in self._refresh(db)
                if include_archived or not entry.archived
            ]

    def get(self, name: str) -> Skill | None:
        skill_slug(name)
        with self._transaction() as db:
            entries = self._refresh(db)
            row = self._find_row(db, name)
            if row is None or row["archived"]:
                return None
            return next(
                (
                    entry.skill
                    for entry in entries
                    if entry.slug == row["slug"] and not entry.archived
                ),
                None,
            )

    def issues(self, name: str) -> tuple[Issue, ...]:
        skill_slug(name)
        with self._transaction() as db:
            entries = self._refresh(db)
            row = self._find_row(db, name)
            slug = row["slug"] if row else skill_slug(name)
            return next(
                (
                    entry.issues
                    for entry in entries
                    if entry.slug.casefold() == slug.casefold()
                ),
                (),
            )

    def find_by_phrase(self, phrase: str) -> Skill | None:
        normalized = " ".join(phrase.casefold().split())
        matches = [
            entry.skill
            for entry in self.list()
            if entry.skill is not None
            and normalized
            in {
                " ".join(value.casefold().split())
                for value in (entry.skill.name, entry.slug, *entry.skill.phrases)
            }
        ]
        return matches[0] if len(matches) == 1 else None

    def catalog(self, *, include_archived: bool = False) -> list[dict]:
        return [
            {
                "slug": entry.slug,
                "name": entry.skill.name,
                "description": entry.skill.description,
                "version": entry.skill.version,
                "source": entry.skill.source,
                "state": entry.skill.state,
                "pinned": entry.skill.pinned,
                "archived": entry.archived,
                "use_count": entry.use_count,
                "failure_count": entry.failure_count,
                "success_count": entry.success_count,
                "last_activity": entry.last_activity,
            }
            for entry in self.list(include_archived=include_archived)
            if entry.skill is not None
        ]

    def versions(self, name: str) -> list[int]:
        with self._transaction() as db:
            self._refresh(db)
            row = self._find_row(db, name)
            if row is None:
                return []
            return [
                version[0]
                for version in db.execute(
                    "SELECT version FROM versions WHERE slug=? ORDER BY version",
                    (row["slug"],),
                )
            ]

    def record_outcome(
        self, name: str, run_id: str, outcome: str, *, at: int | None = None
    ) -> bool:
        """Count one terminal execution; replaying a delivery never counts twice.

        ``use_count`` counts executions, ``success_count`` counts verified done
        outcomes, and ``failure_count`` includes failed, cancelled and uncertain
        outcomes. Loading or editing a skill never counts as execution.
        """
        if outcome not in {"done", "error", "cancelled", "uncertain"}:
            raise ValueError("outcome must be done, error, cancelled or uncertain")
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id must be a nonempty string")
        if at is None:
            at = time.time_ns() // 1_000_000
        if isinstance(at, bool) or not isinstance(at, int) or not 0 <= at < 2**63:
            raise ValueError("at must be a nonnegative millisecond timestamp")
        with self._transaction() as db:
            self._refresh(db)
            row = self._find_row(db, name)
            if row is None:
                return False
            inserted = db.execute(
                "INSERT OR IGNORE INTO outcomes (run_id,slug,outcome,at) VALUES (?,?,?,?)",
                (run_id, row["slug"], outcome, at),
            )
            if not inserted.rowcount:
                return False
            db.execute(
                """UPDATE skills SET use_count=use_count+1,
                success_count=success_count+?, failure_count=failure_count+?,
                last_activity=MAX(last_activity,?) WHERE slug=?""",
                (int(outcome == "done"), int(outcome != "done"), at, row["slug"]),
            )
            return True

    def _version_text(
        self, name: str, version: int, *, original: bool = False
    ) -> str | None:
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValueError("version must be a positive integer")
        with self._transaction() as db:
            self._refresh(db)
            row = self._find_row(db, name)
            if row is None:
                return None
            found = db.execute(
                "SELECT text,original_text FROM versions WHERE slug=? AND version=?",
                (row["slug"], version),
            ).fetchone()
            # Text callers use universal newlines, as Path.read_text does.
            # The database still retains the original line endings verbatim.
            return (
                found["original_text" if original else "text"].replace("\r\n", "\n")
                if found
                else None
            )

    def version_text(self, name: str, version: int) -> str | None:
        return self._version_text(name, version)

    def original_text(self, name: str, version: int) -> str | None:
        """Imported or hand-edited text before normalization, using LF newlines."""
        return self._version_text(name, version, original=True)

    def version(self, name: str, version: int) -> Skill | None:
        text = self.version_text(name, version)
        return self._parse(text) if text is not None else None

    def undo(self, name: str) -> Skill | None:
        with self._transaction() as db:
            entries = self._refresh(db)
            row = self._find_row(db, name)
            if row is None or row["archived"]:
                return None
            entry = next(entry for entry in entries if entry.slug == row["slug"])
            versions = db.execute(
                "SELECT text FROM versions WHERE slug=? ORDER BY version DESC",
                (row["slug"],),
            ).fetchall()
            # A broken unversioned hand edit can be undone even if the skill
            # only had one valid version.
            target = 0 if entry.skill is None else 1
            if len(versions) <= target:
                return None
            previous = replace(self._parse(versions[target]["text"]), state="draft")
            return self._save(db, row["slug"], previous, source="corrected")

    def archive(self, name: str) -> bool:
        with self._transaction() as db:
            self._refresh(db)
            row = self._find_row(db, name)
            if row is None or row["archived"]:
                return False
            self._path(row["slug"]).unlink(missing_ok=True)
            db.execute("UPDATE skills SET archived=1 WHERE slug=?", (row["slug"],))
            return True

    def delete(self, name: str) -> bool:
        """Deletion is archival: no history is destroyed."""
        return self.archive(name)

    def restore(self, name: str) -> Skill | None:
        with self._transaction() as db:
            self._refresh(db)
            row = self._find_row(db, name)
            if row is None:
                return None
            skill = self._parse(row["last_text"])
            if row["archived"]:
                if skill.state != "draft":
                    return self._save(
                        db, row["slug"], replace(skill, state="draft"), source="edited"
                    )
                self._write(row["slug"], row["last_text"])
                db.execute(
                    "UPDATE skills SET archived=0,last_activity=MAX(last_activity,?) WHERE slug=?",
                    (time.time_ns() // 1_000_000, row["slug"]),
                )
            return skill

    def set_pinned(self, name: str, pinned: bool) -> Skill | None:
        if not isinstance(pinned, bool):
            raise ValueError("pinned must be a boolean")
        with self._transaction() as db:
            entries = self._refresh(db)
            row = self._find_row(db, name)
            if row is None:
                return None
            skill = next(entry.skill for entry in entries if entry.slug == row["slug"])
            if skill is None or skill.pinned == pinned:
                return skill
            return self._save(
                db,
                row["slug"],
                replace(skill, pinned=pinned),
                archived=bool(row["archived"]),
            )

    def migrate_legacy(self) -> list[str]:
        with self._transaction() as db:
            entries = self._refresh(db)
            migrated = []
            for entry in entries:
                if entry.archived or entry.skill is None:
                    continue
                row = db.execute(
                    "SELECT last_text FROM skills WHERE slug=?", (entry.slug,)
                ).fetchone()
                if row and is_legacy(row["last_text"]):
                    self._save(db, entry.slug, entry.skill, source="compiled")
                    migrated.append(entry.skill.name)
            return migrated
