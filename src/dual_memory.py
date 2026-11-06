"""Dual Memory System — bounded, file-backed curated memory (MEMORY.md & USER.md).

Port of the Hermes Agent dual memory architecture for IRA.
- MEMORY.md: Durable agent notes, environment facts, workspace info (default budget: 2,200 chars).
- USER.md: Stable user profile, preferences, habits (default budget: 1,375 chars).
- Entries are separated by ENTRY_DELIMITER ("\n§\n").
- Frozen snapshot pattern: System prompt blocks are frozen at session start (prefix cache safe).
  Mid-session writes update disk atomically, but never invalidate the live session prompt prefix.
- Locking & Atomic writes: Cross-platform file locking (msvcrt on Windows, fcntl on Unix) and
  atomic temporary-file replacement.
- Tolerant matching: Typography folding for quotes, dashes, and whitespace normalization.
- Strict budget enforcement: Rejects oversized writes and returns `current_entries` so the model
  can consolidate/prune in a single turn.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any

logger = logging.getLogger("agent.dual_memory")

# Cross-platform file locking primitives
msvcrt = None
fcntl = None
try:
    import fcntl
except ImportError:
    with contextlib.suppress(ImportError):
        import msvcrt

ENTRY_DELIMITER = "\n§\n"

MEMORY_BLOCK_HEADERS = {
    "memory": "MEMORY (your personal notes)",
    "user": "USER PROFILE (who the user is)",
}

DEFAULT_MEMORY_CHAR_LIMIT = 2200
DEFAULT_USER_CHAR_LIMIT = 1375

# Typography folding translation table
_MATCH_FOLD = str.maketrans(
    {
        **dict.fromkeys("\"'`\u2018\u2019\u201a\u201b\u201c\u201d\u201e\u201f", "'"),
        **dict.fromkeys("\u2010\u2011\u2012\u2013\u2014\u2015\u2212", "-"),
    }
)

# Threat patterns for prompt injection/exfiltration
_THREAT_PATTERNS: list[tuple[str, str]] = [
    (r"ignore\s+(?:previous|all|above|prior)\s+instructions", "prompt_injection"),
    (r"system\s+prompt\s+override", "sys_prompt_override"),
    (
        r"disregard\s+(?:your|all|any)\s+(?:instructions|rules|guidelines)",
        "disregard_rules",
    ),
    (
        r"act\s+as\s+(?:if|though)\s+you\s+(?:have\s+no|don\'t\s+have)\s+(?:restrictions|limits|rules)",
        "bypass_restrictions",
    ),
    (
        r"<!--[^>]{0,512}(?:ignore|override|system|secret|hidden)[^>]{0,512}-->",
        "html_comment_injection",
    ),
    (r"<\s*div\s+style\s*=\s*[\"'][^>]{0,2048}display\s*:\s*none", "hidden_div"),
    (r"do\s+not\s+tell\s+the\s+user", "deception_hide"),
    (r"output\s+(?:system|initial)\s+prompt", "leak_system_prompt"),
    (
        r"(?:respond|answer|reply)\s+without\s+(?:restrictions|limitations|filters|safety)",
        "remove_filters",
    ),
]


def scan_threats(content: str) -> str | None:
    """Scan content for obvious injection or exfiltration patterns."""
    text_lower = content.lower()
    for pattern, name in _THREAT_PATTERNS:
        if re.search(pattern, text_lower):
            return f"Blocked: content matched threat pattern '{name}'"
    return None


def normalize_for_match(text: str) -> str:
    """Normalize typography (quotes, dashes, escaped newlines, whitespace runs)."""
    return " ".join(str(text).replace("\\n", " ").translate(_MATCH_FOLD).split())


def find_unique_match(entries: list[str], old_text: str) -> tuple[int | None, bool]:
    """Find index of entry matching old_text.

    Returns:
        (index, is_ambiguous)
    """
    old_text_clean = old_text.strip()
    if not old_text_clean:
        return None, False

    # Exact whole-entry match has absolute priority
    exact = [i for i, e in enumerate(entries) if e.strip() == old_text_clean]
    if exact:
        return exact[0], False

    # Byte-exact substring match
    byte_matches = [i for i, e in enumerate(entries) if old_text_clean in e]
    if len({entries[i] for i in byte_matches}) > 1:
        return None, True
    if len(byte_matches) == 1:
        return byte_matches[0], False

    # Normalized typography-folded substring match
    needle = normalize_for_match(old_text_clean)
    if not any(c.isalnum() for c in needle):
        return None, False

    fuzzy_matches = [
        i for i, e in enumerate(entries) if needle in normalize_for_match(e)
    ]
    if len({entries[i] for i in fuzzy_matches}) > 1:
        return None, True
    if len(fuzzy_matches) == 1:
        return fuzzy_matches[0], False

    return None, False


def default_memories_dir() -> Path:
    """Return default directory for MEMORY.md and USER.md."""
    override = os.environ.get("IRA_MEMORIES_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / "memories"


class DualMemoryStore:
    """Hermes-compatible dual memory store for IRA.

    Maintains MEMORY.md and USER.md with character limits, atomic persistence,
    and a frozen startup prompt snapshot.
    """

    def __init__(
        self,
        memory_dir: str | Path | None = None,
        *,
        memory_char_limit: int = DEFAULT_MEMORY_CHAR_LIMIT,
        user_char_limit: int = DEFAULT_USER_CHAR_LIMIT,
    ) -> None:
        self.memory_dir = (
            Path(memory_dir) if memory_dir is not None else default_memories_dir()
        ).resolve()
        self.memory_char_limit = memory_char_limit
        self.user_char_limit = user_char_limit

        self.memory_entries: list[str] = []
        self.user_entries: list[str] = []

        self._system_prompt_snapshot: dict[str, str] = {"memory": "", "user": ""}
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.load_from_disk()

    # --- Path and Lock helpers ---

    def _path_for(self, target: str) -> Path:
        filename = "USER.md" if target == "user" else "MEMORY.md"
        return self.memory_dir / filename

    @staticmethod
    @contextmanager
    def _file_lock(path: Path):
        """Cross-platform advisory lock on companion .lock file."""
        lock_path = path.with_suffix(path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        if fcntl is None and msvcrt is None:
            yield
            return

        flags = os.O_RDWR | os.O_CREAT
        raw_fd = os.open(lock_path, flags, 0o600)
        try:
            fd = os.fdopen(raw_fd, "r+", encoding="utf-8")
        except Exception:
            os.close(raw_fd)
            raise

        with fd:

            def _flock(unlock: bool):
                if fcntl:
                    fcntl.flock(fd, fcntl.LOCK_UN if unlock else fcntl.LOCK_EX)
                elif msvcrt:
                    fd.seek(0)
                    msvcrt.locking(
                        fd.fileno(), msvcrt.LK_UNLCK if unlock else msvcrt.LK_LOCK, 1
                    )

            _flock(False)
            try:
                yield
            finally:
                with contextlib.suppress(OSError):
                    _flock(True)

    def _write_file_atomic(self, path: Path, entries: list[str]) -> None:
        """Atomically write entries joined by ENTRY_DELIMITER."""
        path.parent.mkdir(parents=True, exist_ok=True)
        content = ENTRY_DELIMITER.join(entries) if entries else ""
        tmp_fd, tmp_path = tempfile.mkstemp(
            prefix=f".{path.name}.", dir=str(path.parent), text=True
        )
        try:
            with open(tmp_fd, "w", encoding="utf-8", newline="\n") as f:
                f.write(content)
            os.replace(tmp_path, path)
        except Exception:
            if os.path.exists(tmp_path):
                with contextlib.suppress(OSError):
                    os.unlink(tmp_path)
            raise

    # --- Parsing and Serialization ---

    @staticmethod
    def _parse_entries(raw: str) -> list[str]:
        if not raw or not raw.strip():
            return []
        parts = raw.split(ENTRY_DELIMITER)
        return [p.strip() for p in parts if p.strip()]

    def _entries_for(self, target: str) -> list[str]:
        return self.user_entries if target == "user" else self.memory_entries

    def _set_entries(self, target: str, entries: list[str]) -> None:
        if target == "user":
            self.user_entries = entries
        else:
            self.memory_entries = entries

    def char_limit(self, target: str) -> int:
        return self.user_char_limit if target == "user" else self.memory_char_limit

    def char_count(self, target: str) -> int:
        entries = self._entries_for(target)
        return len(ENTRY_DELIMITER.join(entries)) if entries else 0

    def usage(self, target: str) -> str:
        count = self.char_count(target)
        limit = self.char_limit(target)
        pct = min(100, int((count / limit) * 100)) if limit > 0 else 0
        return f"{pct}% — {count:,}/{limit:,} chars"

    def _render_block(self, target: str, entries: list[str]) -> str:
        header = MEMORY_BLOCK_HEADERS.get(target, target.upper())
        if not entries:
            return ""
        body = "\n".join(f"- {e}" for e in entries)
        return f"\n# {header}\n\n{body}\n"

    # --- Disk Synchronization & Startup Snapshots ---

    def load_from_disk(self) -> None:
        """Load MEMORY.md and USER.md and freeze the prompt snapshot."""
        for target in ("memory", "user"):
            path = self._path_for(target)
            if not path.exists():
                self._set_entries(target, [])
                self._system_prompt_snapshot[target] = ""
                continue

            try:
                raw = path.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning("Failed to read %s: %s", path.name, e)
                raw = ""

            entries = list(dict.fromkeys(self._parse_entries(raw)))
            self._set_entries(target, entries)

            # Sanitize snapshot entries with threat check
            sanitized: list[str] = []
            for e in entries:
                threat = scan_threats(e)
                if threat:
                    sanitized.append(f"[BLOCKED: entry contained pattern: {threat}]")
                else:
                    sanitized.append(e)

            self._system_prompt_snapshot[target] = self._render_block(target, sanitized)

    def get_system_prompt_snapshot(self, target: str | None = None) -> str:
        """Return the frozen system prompt snapshot captured at session load.

        Mid-session writes do not mutate this snapshot, protecting the LLM prefix cache.
        """
        if target:
            return self._system_prompt_snapshot.get(target, "")
        mem = self._system_prompt_snapshot.get("memory", "")
        usr = self._system_prompt_snapshot.get("user", "")
        return (mem + usr).strip()

    def seed(self) -> list[str]:
        """Return seed user facts for prompt context."""
        return self.list_entries("user")

    def recent(self, limit: int = 5) -> list[str]:
        """Return recent memory entries."""
        entries = self.list_entries("memory")
        return entries[-limit:] if limit > 0 else []

    def list_entries(self, target: str) -> list[str]:
        """Return a copy of the live entries for target ('memory' or 'user')."""
        return list(self._entries_for(target))

    def search(self, query: str, target: str | None = None) -> list[str]:
        """Search entries across user/memory stores for relevant facts.

        Tolerant keyword and substring scoring without any external database.
        """
        raw_query = query.strip()
        if not raw_query:
            return []

        terms = [t.lower() for t in re.findall(r"\w+", raw_query) if len(t) > 1]
        if not terms:
            terms = [raw_query.lower()]

        targets = (
            [target.lower()] if target in ("user", "memory") else ["user", "memory"]
        )
        scored: list[tuple[int, str]] = []
        for tgt in targets:
            for entry in self._entries_for(tgt):
                entry_norm = normalize_for_match(entry).lower()
                entry_words = set(re.findall(r"\w+", entry_norm))
                score = 0
                if raw_query.lower() in entry_norm:
                    score += 3
                for term in terms:
                    if term in entry_words:
                        score += 2
                    elif (
                        any(
                            term.startswith(w) or w.startswith(term)
                            for w in entry_words
                            if len(w) >= 4 and len(term) >= 4
                        )
                        or term in entry_norm
                    ):
                        score += 1
                if score > 0:
                    scored.append((score, entry))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [entry for _, entry in scored]

    # --- Mutations ---

    def _mutate(self, target: str, mutate_fn) -> dict[str, Any]:
        """Execute locked mutation against disk file."""
        target = "user" if target.lower() == "user" else "memory"
        path = self._path_for(target)
        with self._file_lock(path):
            # Reload from disk to prevent clobbering concurrent processes
            if path.exists():
                try:
                    raw = path.read_text(encoding="utf-8")
                    entries = list(dict.fromkeys(self._parse_entries(raw)))
                except Exception as e:
                    return {
                        "success": False,
                        "error": f"Refusing write: {path.name} exists but could not be read ({e}).",
                    }
            else:
                entries = []
            self._set_entries(target, entries)

            result = mutate_fn(entries, self.char_limit(target))
            if isinstance(result, dict):  # Error or abort returned
                return result

            new_entries, msg = result[0], result[1]
            self._set_entries(target, new_entries)
            self._write_file_atomic(path, new_entries)

            return {
                "success": True,
                "message": msg,
                "target": target,
                "usage": self.usage(target),
            }

    def add(self, target: str, content: str) -> dict[str, Any]:
        """Append an entry to target store with budget enforcement and deduplication."""
        target = "user" if target.lower() == "user" else "memory"
        cleaned = " ".join(content.strip().split())
        if not cleaned:
            return {"success": False, "error": "Content cannot be empty."}

        threat = scan_threats(cleaned)
        if threat:
            return {"success": False, "error": threat}

        def _do_add(entries: list[str], limit: int):
            if cleaned in entries:
                return {
                    "success": True,
                    "message": "Entry already exists (no duplicate added).",
                    "target": target,
                    "usage": self.usage(target),
                }

            projected = [*entries, cleaned]
            new_len = len(ENTRY_DELIMITER.join(projected))
            if new_len > limit:
                overflow = new_len - limit
                return {
                    "success": False,
                    "error": (
                        f"Memory at {self.char_count(target):,}/{limit:,} chars; adding this entry ({len(cleaned)} chars) "
                        f"would exceed the limit by {overflow:,} chars. Consolidate or remove entries to free space."
                    ),
                    "target": target,
                    "usage": self.usage(target),
                    "current_entries": entries,
                }
            return projected, "Entry added."

        return self._mutate(target, _do_add)

    def patch(self, target: str, old_text: str, new_text: str) -> dict[str, Any]:
        """Patch a specific substring within an existing entry."""
        target = "user" if target.lower() == "user" else "memory"
        if not (old_text or "").strip():
            return {"success": False, "error": "old_text cannot be empty."}
        if new_text is None or not str(new_text).strip():
            return {
                "success": False,
                "error": "new_text cannot be empty. Use remove to delete an entire entry.",
            }

        threat = scan_threats(new_text)
        if threat:
            return {"success": False, "error": threat}

        def _do_patch(entries: list[str], limit: int):
            idx, is_ambiguous = find_unique_match(entries, old_text)
            if is_ambiguous:
                return {
                    "success": False,
                    "error": f"Multiple entries match '{old_text}'. Provide a more specific match string.",
                    "target": target,
                    "current_entries": entries,
                }
            if idx is None:
                return {
                    "success": False,
                    "error": f"No entry matched '{old_text}'.",
                    "target": target,
                    "current_entries": entries,
                }

            before = entries[idx]
            if before.count(old_text) > 1:
                return {
                    "success": False,
                    "error": (
                        f"'{old_text}' appears multiple times in the matched entry. "
                        "Include more surrounding context so the match is unique."
                    ),
                    "target": target,
                }

            after = before.replace(old_text, str(new_text), 1)
            if after == before:
                return {
                    "success": False,
                    "error": f"Patch is a no-op: '{old_text}' -> '{new_text}' leaves entry unchanged.",
                }

            projected = [*entries[:idx], after, *entries[idx + 1 :]]
            new_len = len(ENTRY_DELIMITER.join(projected))
            if new_len > limit:
                overflow = new_len - limit
                return {
                    "success": False,
                    "error": f"Patch would put memory at {new_len:,}/{limit:,} chars (exceeds limit by {overflow:,} chars).",
                    "target": target,
                    "usage": self.usage(target),
                    "current_entries": entries,
                }

            return projected, "Entry patched."

        return self._mutate(target, _do_patch)

    def replace(self, target: str, old_text: str, new_content: str) -> dict[str, Any]:
        """Replace the entire entry containing old_text with new_content."""
        target = "user" if target.lower() == "user" else "memory"
        if not (old_text or "").strip():
            return {"success": False, "error": "old_text cannot be empty."}
        cleaned_content = " ".join(new_content.strip().split())
        if not cleaned_content:
            return {
                "success": False,
                "error": "new_content cannot be empty. Use remove to delete entries.",
            }

        threat = scan_threats(cleaned_content)
        if threat:
            return {"success": False, "error": threat}

        def _do_replace(entries: list[str], limit: int):
            idx, is_ambiguous = find_unique_match(entries, old_text)
            if is_ambiguous:
                return {
                    "success": False,
                    "error": f"Multiple entries match '{old_text}'. Provide a more specific match string.",
                    "target": target,
                    "current_entries": entries,
                }
            if idx is None:
                return {
                    "success": False,
                    "error": f"No entry matched '{old_text}'.",
                    "target": target,
                    "current_entries": entries,
                }

            projected = [*entries[:idx], cleaned_content, *entries[idx + 1 :]]
            new_len = len(ENTRY_DELIMITER.join(projected))
            if new_len > limit:
                overflow = new_len - limit
                return {
                    "success": False,
                    "error": f"Replacement would put memory at {new_len:,}/{limit:,} chars (exceeds limit by {overflow:,} chars).",
                    "target": target,
                    "usage": self.usage(target),
                    "current_entries": entries,
                }
            return projected, "Entry replaced."

        return self._mutate(target, _do_replace)

    def remove(self, target: str, old_text: str) -> dict[str, Any]:
        """Remove the entry containing old_text."""
        target = "user" if target.lower() == "user" else "memory"
        if not (old_text or "").strip():
            return {"success": False, "error": "old_text cannot be empty."}

        def _do_remove(entries: list[str], limit: int):
            idx, is_ambiguous = find_unique_match(entries, old_text)
            if is_ambiguous:
                return {
                    "success": False,
                    "error": f"Multiple entries match '{old_text}'. Provide a more specific match string.",
                    "target": target,
                    "current_entries": entries,
                }
            if idx is None:
                return {
                    "success": False,
                    "error": f"No entry matched '{old_text}'.",
                    "target": target,
                    "current_entries": entries,
                }

            removed = entries[idx]
            projected = entries[:idx] + entries[idx + 1 :]
            return projected, f"Entry removed: '{removed[:60]}...'"

        return self._mutate(target, _do_remove)

    def batch(self, target: str, operations: list[dict[str, Any]]) -> dict[str, Any]:
        """Execute a list of atomic operations (add, patch, replace, remove) against target."""
        target = "user" if target.lower() == "user" else "memory"
        if not operations:
            return {
                "success": True,
                "message": "No operations provided.",
                "target": target,
            }

        def _do_batch(entries: list[str], limit: int):
            current = list(entries)
            for op in operations:
                action = op.get("action")
                if action == "add":
                    content = " ".join(op.get("content", "").strip().split())
                    if not content:
                        return {
                            "success": False,
                            "error": "Batch op 'add' missing content.",
                        }
                    threat = scan_threats(content)
                    if threat:
                        return {"success": False, "error": threat}
                    if content not in current:
                        current.append(content)
                elif action == "remove":
                    old_text = op.get("old_text", "")
                    idx, is_ambig = find_unique_match(current, old_text)
                    if is_ambig or idx is None:
                        return {
                            "success": False,
                            "error": f"Batch remove failed: match for '{old_text}' not unique or not found.",
                        }
                    current.pop(idx)
                elif action == "replace":
                    old_text = op.get("old_text", "")
                    new_content = " ".join(op.get("new_content", "").strip().split())
                    if not new_content:
                        return {
                            "success": False,
                            "error": "Batch replace missing new_content.",
                        }
                    threat = scan_threats(new_content)
                    if threat:
                        return {"success": False, "error": threat}
                    idx, is_ambig = find_unique_match(current, old_text)
                    if is_ambig or idx is None:
                        return {
                            "success": False,
                            "error": f"Batch replace failed: match for '{old_text}' not unique or not found.",
                        }
                    current[idx] = new_content
                elif action == "patch":
                    old_text = op.get("old_text", "")
                    new_text = op.get("new_text", "")
                    idx, is_ambig = find_unique_match(current, old_text)
                    if is_ambig or idx is None:
                        return {
                            "success": False,
                            "error": f"Batch patch failed: match for '{old_text}' not unique or not found.",
                        }
                    before = current[idx]
                    after = before.replace(old_text, new_text, 1)
                    current[idx] = after
                else:
                    return {
                        "success": False,
                        "error": f"Unknown batch action: {action}",
                    }

            new_len = len(ENTRY_DELIMITER.join(current))
            if new_len > limit:
                return {
                    "success": False,
                    "error": f"Batch operations would put memory at {new_len:,}/{limit:,} chars.",
                    "target": target,
                    "usage": self.usage(target),
                    "current_entries": entries,
                }
            return (
                current,
                f"Successfully executed batch of {len(operations)} operations.",
            )

        return self._mutate(target, _do_batch)
