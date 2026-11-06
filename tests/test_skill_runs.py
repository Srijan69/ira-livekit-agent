import sqlite3

import pytest

from skill_runs import SkillRunStore


def record(run_id, status="running"):
    return {
        "run_id": run_id,
        "skill_slug": "music",
        "skill_name": "Music",
        "skill_version": 3,
        "status": status,
        "started_at": 100,
        "finished_at": None,
        "completed_steps": 0,
        "total_steps": 2,
        "failed_step": None,
        "verified": False,
        "message": "",
        "steps": [],
        "skill_markdown": "immutable source",
    }


def test_run_records_are_durable_and_terminal_results_cannot_be_overwritten(tmp_path):
    path = tmp_path / "runs.sqlite3"
    store = SkillRunStore(path)
    assert store.create(record("delivery"))
    assert not store.create(record("delivery"))
    store.record_step(
        "delivery", {"step_index": 1, "outcome": "done", "evidence": "window 42"}
    )
    store.update("delivery", status="uncertain", message="deadline", finished_at=120)
    store.update("delivery", status="done", verified=True)
    reopened = SkillRunStore(path)
    persisted = reopened.get("delivery")
    assert persisted["status"] == "uncertain" and not persisted["verified"]
    assert persisted["steps"][0]["evidence"] == "window 42"
    assert persisted["skill_markdown"] == "immutable source"
    assert reopened.list()[0] == persisted


def test_recovery_marks_only_incomplete_runs_uncertain_and_is_not_a_read_side_effect(
    tmp_path,
):
    path = tmp_path / "runs.sqlite3"
    store = SkillRunStore(path)
    store.create(record("running"))
    store.create(record("complete", "done"))
    assert SkillRunStore(path).get("running")["status"] == "running"
    assert store.recover_interrupted() == 1
    assert store.get("running")["status"] == "uncertain"
    assert store.get("running")["finished_at"] >= 100
    assert store.get("complete")["status"] == "done"


def test_sqlite_handles_are_closed_after_operations(tmp_path, monkeypatch):
    connect = sqlite3.connect
    connections = []

    def track_connection(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", track_connection)
    store = SkillRunStore(tmp_path / "runs.db")
    store.create(record("one"))
    assert store.get("one")["run_id"] == "one"
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")


@pytest.mark.parametrize("recovery", [False, True])
def test_terminal_transition_clears_pending_question_and_cannot_reopen_it(
    tmp_path, recovery
):
    store = SkillRunStore(tmp_path / "runs.db")
    waiting = record("paused", "waiting")
    waiting.update(
        question="Which account?", question_id="question-one", input_name="account"
    )
    store.create(waiting)
    if recovery:
        assert store.recover_interrupted() == 1
    else:
        store.update("paused", status="cancelled")
    store.update("paused", status="waiting", question="Again?", question_id="late")
    result = store.get("paused")
    assert result["status"] == ("uncertain" if recovery else "cancelled")
    assert result["question"] == result["question_id"] == result["input_name"] == ""
