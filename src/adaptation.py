"""Background adaptation and self-improving system for IRA.

Port of the Hermes Agent adaptation & background review architecture for IRA.
- Non-blocking: background review runs without impacting realtime voice latency.
- Ground truth verification: Only verified successful runs (status='done', verified=True)
  can be distilled into learned skills.
- Isolation: Does not mutate active conversation context or prompt prefix cache.
- Continuous maintenance: Coordinates with SkillCurator for inactivity transitions (stale, archive)
  and consolidation clustering.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from curator import SkillCurator
from dual_memory import DualMemoryStore
from skill_learning import learn_from_run
from skill_runs import SkillRunStore
from skill_store import SkillStore

logger = logging.getLogger("agent.adaptation")


class AdaptationEngine:
    """Coordinates background review, procedural distillation, and library curation."""

    def __init__(
        self,
        skill_store: SkillStore,
        run_store: SkillRunStore,
        dual_memory: DualMemoryStore,
        curator: SkillCurator | None = None,
    ) -> None:
        self.skill_store = skill_store
        self.run_store = run_store
        self.dual_memory = dual_memory
        self.curator = curator or SkillCurator(skill_store)
        self._lock = threading.Lock()

    def evaluate_completed_run(
        self, run_id: str, proposed_skill_name: str | None = None
    ) -> dict[str, Any]:
        """Evaluate a finished skill run for procedural learning.

        Strictly gates on verified success (status='done', verified=True).
        """
        record = self.run_store.get(run_id) if run_id else None
        if not record:
            return {"status": "skipped", "reason": "No run record found."}

        if record.get("status") != "done" or record.get("verified") is not True:
            return {
                "status": "skipped",
                "reason": "Run was not verified as successful; self-improvement requires ground truth.",
            }

        name = (
            proposed_skill_name or record.get("skill_name") or f"procedure-{run_id[:8]}"
        )
        try:
            skill = learn_from_run(run_id, name, self.skill_store, self.run_store)
            logger.info(
                "Adaptation distilled new draft skill: %s (run %s)",
                skill.name,
                run_id,
            )
            return {
                "status": "learned",
                "skill_name": skill.name,
                "version": skill.version,
                "state": skill.state,
            }
        except ValueError as exc:
            return {"status": "declined", "reason": str(exc)}

    def run_idle_curation(self, now: float | None = None) -> dict[str, Any]:
        """Perform library lifecycle maintenance and identify consolidation clusters."""
        transitions = self.curator.apply_automatic_transitions(now=now)
        clusters = self.curator.find_consolidation_candidates()
        suggestions = self.curator.suggest(now=now)

        return {
            "status": "completed",
            "transitions": transitions,
            "cluster_count": len(clusters),
            "clusters": clusters,
            "suggestions": suggestions,
        }

    async def schedule_background_run_review(
        self, run_id: str, proposed_name: str | None = None
    ) -> None:
        """Schedule evaluation in an asynchronous thread so voice response never pauses."""
        await asyncio.to_thread(self.evaluate_completed_run, run_id, proposed_name)
