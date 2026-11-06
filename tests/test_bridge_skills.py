"""Management is authenticated and never starts a desktop executor."""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bridge import SHARED_SECRET_HEADER, BridgeConfig, create_app
from skill_format import Action, Skill, Step
from skill_store import SkillStore
from tasks import TaskStore

AUTH = {SHARED_SECRET_HEADER: "skills-secret"}


@pytest.fixture
async def skills_client(tmp_path):
    store = SkillStore(tmp_path / "skills")
    store.save(
        Skill(
            name="Open Notes",
            description="Open a notes app",
            steps=(
                Step(
                    1,
                    "Open notes",
                    action=Action(
                        "tool", tool="open_app", args=(("app_name", "Notepad"),)
                    ),
                ),
            ),
        ),
        source="dictated",
    )
    config = BridgeConfig("", "", "", "skills-secret")
    app = create_app(
        config,
        tasks=TaskStore(tmp_path / "tasks.db"),
        skills=store,
        run_path=tmp_path / "runs.db",
    )
    async with TestClient(TestServer(app)) as client:
        yield client


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/skills"),
        ("get", "/skills/history"),
        ("get", "/skills/suggestions"),
        ("get", "/skills/open-notes"),
        ("patch", "/skills/open-notes"),
        ("delete", "/skills/open-notes"),
        ("post", "/skills/open-notes/restore"),
    ],
)
async def test_all_management_requires_secret(skills_client, method, path):
    response = await getattr(skills_client, method)(path, json={})
    assert response.status == 401


async def test_catalog_detail_and_recoverable_archive(skills_client):
    client = skills_client
    response = await client.get("/skills", headers=AUTH)
    skill = (await response.json())["skills"][0]
    assert skill["slug"] == "open-notes"
    assert skill["state"] == "draft"
    detail = await (await client.get("/skills/open-notes", headers=AUTH)).json()
    assert "Open Notes" in detail["skill"]["markdown"]
    assert detail["skill"]["versions"] == [1]
    response = await client.patch(
        "/skills/open-notes", headers=AUTH, json={"pinned": True}
    )
    assert response.status == 200
    assert (await response.json())["skill"]["pinned"] is True
    assert (await client.delete("/skills/open-notes", headers=AUTH)).status == 200
    assert (await (await client.get("/skills", headers=AUTH)).json())["skills"] == []
    archived = (
        await (await client.get("/skills?include_archived=true", headers=AUTH)).json()
    )["skills"]
    assert archived[0]["archived"] is True
    assert (await client.post("/skills/open-notes/restore", headers=AUTH)).status == 200
    restored = (await (await client.get("/skills/open-notes", headers=AUTH)).json())[
        "skill"
    ]
    assert restored["pinned"] is True
    assert restored["versions"]


@pytest.mark.parametrize(
    "body",
    [
        [],
        None,
        {},
        {"pinned": "false"},
        {"pinned": True, "run": True},
        {"state": "ready"},
    ],
)
async def test_rejects_malformed_mutations(skills_client, body):
    response = await skills_client.patch("/skills/open-notes", headers=AUTH, json=body)
    assert response.status == 400


async def test_history_is_bounded_and_execution_is_not_an_http_route(skills_client):
    client = skills_client
    assert (await (await client.get("/skills/history", headers=AUTH)).json()) == {
        "runs": []
    }
    assert (await client.get("/skills/history?limit=10000", headers=AUTH)).status == 400
    assert (await client.get("/skills/not-found", headers=AUTH)).status == 404
    assert (await client.post("/skills/open-notes/run", headers=AUTH)).status == 404


async def test_version_inspection_returns_immutable_text_and_diff(skills_client):
    client = skills_client
    await client.patch("/skills/open-notes", headers=AUTH, json={"pinned": True})
    response = await client.get("/skills/open-notes?version=1", headers=AUTH)
    skill = (await response.json())["skill"]
    assert skill["selected_version"] == 1
    assert "pinned: false" in skill["markdown"]
    assert "+pinned: true" in skill["diff"]
    assert (
        await client.get("/skills/open-notes?version=bad", headers=AUTH)
    ).status == 400
    assert (
        await client.get("/skills/open-notes?version=999", headers=AUTH)
    ).status == 404


async def test_bridge_observes_live_runs_without_recovering_or_archiving(
    skills_client, tmp_path
):
    from skill_runs import SkillRunStore

    runs = SkillRunStore(tmp_path / "runs.db")
    runs.create({"run_id": "active", "status": "running", "started_at": 123})
    history = await (await skills_client.get("/skills/history", headers=AUTH)).json()
    assert history["runs"][0]["status"] == "running"
    assert runs.get("active")["status"] == "running"
    suggestions = await (
        await skills_client.get("/skills/suggestions", headers=AUTH)
    ).json()
    assert suggestions == {"suggestions": []}
    assert (await (await skills_client.get("/skills", headers=AUTH)).json())["skills"]
