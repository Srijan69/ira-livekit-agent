"""Curator — background skill maintenance and lifecycle orchestrator.

Port of the Hermes Agent Curator architecture for IRA.
- Invariants:
  - Pinned skills (pinned=True) bypass all automatic transitions.
  - Never permanently delete: skills are archived (recoverable via store.restore()).
  - Transitions are deterministic based on activity timestamps:
    - Inactive >= 14 days: marked as stale / review recommended.
    - Inactive >= 30 days: auto-archived to .archive / recoverable state.
    - Reactivation: if an archived/stale skill is used, it returns to active.
  - Diagnostics: detects skills with failure_count >= 3 and failure_count > success_count.
  - Persistence: scheduler and execution telemetry saved in .curator_state JSON.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("agent.curator")

DEFAULT_STALE_DAYS = 14
DEFAULT_ARCHIVE_DAYS = 30


class SkillCurator:
    """Manages skill lifecycles, archival, and health diagnostics."""

    def __init__(self, store, state_file: Path | str | None = None) -> None:
        self.store = store
        self.state_file = (
            Path(state_file)
            if state_file is not None
            else self.store.root / ".curator_state"
        )

    # --- Persistent State (.curator_state) ---

    def load_state(self) -> dict[str, Any]:
        base: dict[str, Any] = {
            "last_run_at": None,
            "last_run_summary": None,
            "run_count": 0,
            "paused": False,
        }
        if not self.state_file.exists():
            return base
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                base.update(data)
        except Exception as e:
            logger.warning("Could not read curator state: %s", e)
        return base

    def save_state(self, state: dict[str, Any]) -> None:
        tmp_fd, tmp_path = tempfile.mkstemp(
            prefix=".curator_state.", dir=str(self.state_file.parent), text=True
        )
        try:
            with open(tmp_fd, "w", encoding="utf-8", newline="\n") as f:
                json.dump(state, f, indent=2)
            os.replace(tmp_path, self.state_file)
        except Exception as e:
            logger.warning("Could not save curator state: %s", e)
            if os.path.exists(tmp_path):
                with contextlib.suppress(OSError):
                    os.unlink(tmp_path)

    def is_paused(self) -> bool:
        return bool(self.load_state().get("paused"))

    def set_paused(self, paused: bool) -> None:
        state = self.load_state()
        state["paused"] = bool(paused)
        self.save_state(state)

    # --- Lifecycle Transitions & Maintenance ---

    def apply_automatic_transitions(
        self,
        *,
        now: float | None = None,
        stale_days: float = DEFAULT_STALE_DAYS,
        archive_days: float = DEFAULT_ARCHIVE_DAYS,
    ) -> dict[str, Any]:
        """Apply deterministic inactivity transitions (stale, archive).

        Never modifies or archives pinned skills.
        """
        if self.is_paused():
            return {"status": "paused", "archived": 0, "stale": 0}

        now_ms = time.time() * 1000 if now is None else now
        counts = {"archived": 0, "stale": 0, "reactivated": 0}
        archived_slugs: list[str] = []

        catalog = self.store.catalog(include_archived=True)
        for row in catalog:
            if row.get("pinned"):
                continue

            last_act = row.get("last_activity") or 0
            days_inactive = max(0.0, (now_ms - last_act) / 86_400_000.0)
            is_archived = bool(row.get("archived"))

            if not is_archived and days_inactive >= archive_days:
                # Inactive for >= 30 days: auto-archive (recoverable)
                if self.store.archive(row["slug"]):
                    counts["archived"] += 1
                    archived_slugs.append(row["slug"])
            elif not is_archived and days_inactive >= stale_days:
                counts["stale"] += 1

        state = self.load_state()
        state["last_run_at"] = now_ms
        state["run_count"] = state.get("run_count", 0) + 1
        state["last_run_summary"] = (
            f"Archived {counts['archived']} skills, {counts['stale']} stale skills detected."
        )
        self.save_state(state)

        return {
            "status": "completed",
            "archived": counts["archived"],
            "stale": counts["stale"],
            "archived_slugs": archived_slugs,
        }

    # --- Diagnostics & Suggestions ---

    def suggest(
        self,
        *,
        now: float | None = None,
        stale_days: float = DEFAULT_STALE_DAYS,
        archive_days: float = DEFAULT_ARCHIVE_DAYS,
    ) -> list[dict[str, Any]]:
        """Return review suggestions and diagnostic warnings."""
        now_ms = time.time() * 1000 if now is None else now
        suggestions: list[dict[str, Any]] = []

        for row in self.store.catalog(include_archived=False):
            if row.get("pinned"):
                continue

            last_act = row.get("last_activity") or 0
            days = max(0.0, (now_ms - last_act) / 86_400_000.0)
            reason = None
            action = "review"

            if days >= archive_days:
                reason = f"No activity for {int(days)} days; eligible for archival."
                action = "consider_archive"
            elif row.get("failure_count", 0) >= 3 and row.get(
                "failure_count", 0
            ) > row.get("success_count", 0):
                reason = (
                    f"High failure rate ({row['failure_count']} fails vs "
                    f"{row['success_count']} successes); inspect checks and tools."
                )
                action = "inspect_failures"
            elif days >= stale_days:
                reason = f"No recent activity ({int(days)} days); verify procedure is still relevant."
                action = "review"

            if reason:
                suggestions.append(
                    {
                        "slug": row["slug"],
                        "name": row["name"],
                        "action": action,
                        "reason": reason,
                        "days_inactive": round(days, 1),
                    }
                )

        return suggestions

    def find_consolidation_candidates(self) -> list[dict[str, Any]]:
        """Cluster skills by name prefix / domain to identify consolidation opportunities."""
        active_skills = [
            s for s in self.store.catalog(include_archived=False) if not s.get("pinned")
        ]
        clusters: dict[str, list[dict[str, Any]]] = {}

        for skill in active_skills:
            slug = skill["slug"]
            prefix = slug.split("-")[0] if "-" in slug else slug
            if len(prefix) >= 3:
                clusters.setdefault(prefix, []).append(skill)

        candidates = []
        for prefix, members in clusters.items():
            if len(members) >= 2:
                candidates.append(
                    {
                        "cluster_prefix": prefix,
                        "skill_count": len(members),
                        "slugs": [m["slug"] for m in members],
                        "recommendation": (
                            f"Consolidate {len(members)} narrow '{prefix}-*' skills into a "
                            f"single umbrella skill with labeled subsections."
                        ),
                    }
                )

        return candidates
