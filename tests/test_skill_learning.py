from dataclasses import replace

import pytest

from skill_format import Action, Check, Skill, Step, render_skill
from skill_store import SkillStore


def demonstration():
    return Skill(
        name="Open notes",
        description="Opens notes.",
        state="enabled",
        allowed_tools=("open_app",),
        steps=(
            Step(
                1,
                "Open",
                action=Action("tool", tool="open_app", args=(("app_name", "Notepad"),)),
                check=Check("window_title", "Notepad"),
            ),
        ),
    )


class Runs:
    def __init__(self, record):
        self.record = record

    def get(self, run_id):
        return self.record


def test_learning_preserves_checks_and_provenance_but_never_enables(tmp_path):
    from skill_learning import learn_from_run

    store = SkillStore(tmp_path)
    runs = Runs(
        {
            "status": "done",
            "verified": True,
            "skill_markdown": render_skill(demonstration()),
        }
    )
    learned = learn_from_run("run-1", "My notes", store, runs)
    assert learned.state == "draft"
    assert learned.source_run_id == "run-1"
    assert learned.allowed_tools == ("open_app",)
    assert learned.steps[0].check == Check("window_title", "Notepad")


@pytest.mark.parametrize(
    "status,verified", [("error", False), ("uncertain", False), ("done", False)]
)
def test_failed_or_unverified_demonstrations_cannot_be_learned(
    tmp_path, status, verified
):
    from skill_learning import learn_from_run

    store = SkillStore(tmp_path)
    with pytest.raises(ValueError, match="verified"):
        learn_from_run(
            "run-1", "New", store, Runs({"status": status, "verified": verified})
        )
    assert store.list() == []


def test_learning_refuses_silent_overwrite(tmp_path):
    from skill_learning import learn_from_run

    store = SkillStore(tmp_path)
    store.save(demonstration())
    with pytest.raises(ValueError, match="already"):
        learn_from_run(
            "run-1",
            "Open notes",
            store,
            Runs(
                {
                    "status": "done",
                    "verified": True,
                    "skill_markdown": render_skill(demonstration()),
                }
            ),
        )
    assert store.get("open-notes").version == 1


def test_curator_only_suggests_archival_and_honors_pins(tmp_path):
    from curator import SkillCurator

    store = SkillStore(tmp_path)
    store.save(demonstration())
    store.save(replace(demonstration(), name="Pinned", pinned=True))
    suggestions = SkillCurator(store).suggest(now=10**15)
    assert [s["slug"] for s in suggestions] == ["open-notes"]
    assert store.get("open-notes") is not None
    assert store.get("pinned").pinned
