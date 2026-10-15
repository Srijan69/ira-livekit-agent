from __future__ import annotations

import pytest

from workflows import WorkflowStore, classify_workflow


def test_workflow_survives_reopening_and_renders_bounded_instructions(tmp_path):
    store = WorkflowStore(tmp_path)
    store.save(
        "Open Spotify and play my focus playlist",
        "Start the focus playlist in the signed-in browser",
        ["Open Spotify in Chrome", "Open the focus playlist", "Press play"],
        notes="Use the boss's existing browser session.",
    )

    reopened = WorkflowStore(tmp_path)
    workflow = reopened.get("open spotify and play my focus playlist")

    assert workflow is not None
    assert workflow.steps == [
        "Open Spotify in Chrome",
        "Open the focus playlist",
        "Press play",
    ]
    rendered = reopened.render(workflow)
    assert "Start the focus playlist" in rendered
    assert "Open Spotify in Chrome" in rendered
    assert len(rendered) <= 2000


def test_workflow_names_are_slugged_and_replaced(tmp_path):
    store = WorkflowStore(tmp_path)
    store.save("Daily / Build Check", "Check the build", ["Run tests"])
    store.save(
        "Daily / Build Check", "Check the build again", ["Run tests", "Read failures"]
    )

    workflows = store.list()
    assert len(workflows) == 1
    assert workflows[0].slug == "daily-build-check"
    assert store.get("daily-build-check").steps[-1] == "Read failures"


def test_delete_reports_missing_and_removed_workflows(tmp_path):
    store = WorkflowStore(tmp_path)
    assert "not found" in store.delete("missing").lower()
    store.save("Cleanup", "Clean up", ["Close the app"])
    assert "deleted" in store.delete("cleanup").lower()
    assert store.get("cleanup") is None


def test_open_folder_creates_and_opens_the_workflow_directory(tmp_path):
    opened = []
    store = WorkflowStore(tmp_path / "workflows", opener=opened.append)

    result = store.open_folder()

    assert store.root.is_dir()
    assert opened == [store.root]
    assert "opened" in result.lower()


def test_detailed_workflow_round_trips_and_classifies_as_lightweight(tmp_path):
    store = WorkflowStore(tmp_path)
    store.save(
        "Play focus music",
        "Play the saved focus playlist",
        ["Open Spotify in Chrome", "Press play"],
        allowed_tools=["open_spotify", "spotify_control"],
        prerequisites=["IRA browser profile is signed in"],
        verification=["Spotify reports that playback is active"],
        recovery=["Stop and report the failed step"],
    )

    workflow = store.get("play focus music")

    assert workflow is not None
    assert workflow.allowed_tools == ["open_spotify", "spotify_control"]
    assert workflow.prerequisites == ["IRA browser profile is signed in"]
    assert workflow.verification == ["Spotify reports that playback is active"]
    assert classify_workflow(workflow) == "lightweight"
    assert "Strict execution" in store.render(workflow)


@pytest.mark.parametrize(
    "metadata",
    [
        {"requires_screen": True},
        {"linear": False},
        {"failure_policy": "retry"},
        {"allowed_tools": ["interpret_screen"]},
    ],
)
def test_complex_workflow_routes_to_powerful_worker(tmp_path, metadata):
    store = WorkflowStore(tmp_path)
    store.save("Complex", "Do complex work", ["Do the work"], **metadata)
    assert classify_workflow(store.get("complex")) == "powerful"


def test_more_than_five_steps_routes_to_powerful_worker(tmp_path):
    store = WorkflowStore(tmp_path)
    store.save("Long", "Do six steps", [f"Step {index}" for index in range(6)])
    assert classify_workflow(store.get("long")) == "powerful"


def test_predict_matches_a_natural_language_request(tmp_path):
    store = WorkflowStore(tmp_path)
    store.save(
        "Play my favorite Spotify funk songs",
        "Start the boss's favorite funk playlist in Spotify",
        [
            "open_spotify: browser=chrome",
            "spotify_play_playlist: name=my funk songs",
        ],
        notes="Use the signed-in browser profile.",
    )
    store.save(
        "Close Spotify",
        "Close the Spotify browser window",
        ["close_browser:"],
    )

    prediction = store.predict("please start my favorite funk music in Spotify")

    assert prediction is not None
    assert prediction.workflow.name == "Play my favorite Spotify funk songs"
    assert prediction.confidence >= 0.5
    assert "spotify" in prediction.matched_terms


def test_predict_returns_none_for_an_unrelated_or_ambiguous_request(tmp_path):
    store = WorkflowStore(tmp_path)
    store.save("Open Spotify", "Open Spotify", ["open_spotify: browser=chrome"])
    store.save("Open Chrome", "Open Chrome", ["open_app: app_name=Chrome"])

    assert store.predict("what is the weather today") is None
    assert store.predict("open an app") is None
