"""Tests for Hermes-aligned AdaptationEngine in IRA."""

from pathlib import Path

import pytest

from adaptation import AdaptationEngine
from curator import SkillCurator
from dual_memory import DualMemoryStore
from skill_format import Action, Check, Skill, Step, render_skill
from skill_runs import SkillRunStore
from skill_store import SkillStore


def _seed_run(
    run_store: SkillRunStore, run_id: str, status: str, verified: bool
) -> None:
    skill = Skill(
        name="Auto Test Skill",
        description="A skill for automated testing",
        goal="Complete task",
        steps=(
            Step(
                number=1,
                purpose="Inspect status",
                action=Action(kind="tool", tool="system_status"),
                check=Check(kind="window_title", value="Status"),
            ),
        ),
        state="enabled",
    )
    run_store.create(
        {
            "run_id": run_id,
            "status": status,
            "started_at": 100.0,
            "verified": verified,
            "skill_markdown": render_skill(skill),
            "skill_name": skill.name,
        }
    )


@pytest.mark.asyncio
async def test_adaptation_rejects_unverified_run(tmp_path: Path):
    skill_store = SkillStore(tmp_path / "workflows")
    run_store = SkillRunStore(tmp_path / "runs.db")
    dual_memory = DualMemoryStore(tmp_path / "memories")
    engine = AdaptationEngine(skill_store, run_store, dual_memory)

    _seed_run(run_store, "run-failed-01", status="error", verified=False)
    result = engine.evaluate_completed_run("run-failed-01")
    assert result["status"] == "skipped"
    assert "not verified" in result["reason"]

    _seed_run(run_store, "run-unverified-02", status="done", verified=False)
    result2 = engine.evaluate_completed_run("run-unverified-02")
    assert result2["status"] == "skipped"


@pytest.mark.asyncio
async def test_adaptation_learns_from_verified_run(tmp_path: Path):
    skill_store = SkillStore(tmp_path / "workflows")
    run_store = SkillRunStore(tmp_path / "runs.db")
    dual_memory = DualMemoryStore(tmp_path / "memories")
    engine = AdaptationEngine(skill_store, run_store, dual_memory)

    _seed_run(run_store, "run-verified-03", status="done", verified=True)
    result = engine.evaluate_completed_run(
        "run-verified-03", proposed_skill_name="Learned System Inspection"
    )
    assert result["status"] == "learned"
    assert result["skill_name"] == "Learned System Inspection"
    assert result["state"] == "draft"

    # Verify skill is saved in skill_store
    stored = skill_store.get("Learned System Inspection")
    assert stored is not None
    assert stored.source == "learned"


@pytest.mark.asyncio
async def test_adaptation_idle_curation(tmp_path: Path):
    skill_store = SkillStore(tmp_path / "workflows")
    run_store = SkillRunStore(tmp_path / "runs.db")
    dual_memory = DualMemoryStore(tmp_path / "memories")
    curator = SkillCurator(skill_store)
    engine = AdaptationEngine(skill_store, run_store, dual_memory, curator=curator)

    curation_summary = engine.run_idle_curation()
    assert curation_summary["status"] == "completed"
    assert "transitions" in curation_summary
    assert "cluster_count" in curation_summary


@pytest.mark.asyncio
async def test_adaptation_async_schedule(tmp_path: Path):
    skill_store = SkillStore(tmp_path / "workflows")
    run_store = SkillRunStore(tmp_path / "runs.db")
    dual_memory = DualMemoryStore(tmp_path / "memories")
    engine = AdaptationEngine(skill_store, run_store, dual_memory)

    _seed_run(run_store, "run-async-04", status="done", verified=True)
    await engine.schedule_background_run_review("run-async-04", "Async Procedure")
    assert skill_store.get("Async Procedure") is not None
