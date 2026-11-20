from aiohttp.test_utils import TestClient, TestServer
from test_task_routing import RESEARCH, request

from bridge import SHARED_SECRET_HEADER, BridgeConfig, create_app
from skill_store import SkillStore
from skills import SkillCatalog
from tasks import TaskStore


async def test_dashboard_catalog_and_detail_use_same_procedural_library(
    tmp_path, monkeypatch
):
    catalog = SkillCatalog(tmp_path / "skills")
    catalog.create_skill("research-topic", RESEARCH, category="web")
    monkeypatch.setenv("IRA_SKILLS_DIR", str(catalog.root))
    app = create_app(
        BridgeConfig("", "", "", "test-secret"),
        tasks=TaskStore(tmp_path / "tasks.db"),
        skills=SkillStore(tmp_path / "workflows"),
    )
    async with TestClient(TestServer(app)) as client:
        headers = {SHARED_SECRET_HEADER: "test-secret"}
        assert (await client.get("/skills")).status == 401
        rows = (await (await client.get("/skills", headers=headers)).json())["skills"]
        assert len(rows) == 1
        row = rows[0]
        assert row["mode"] == "procedure"
        assert row["procedure_name"] == "procedure:web/research-topic"
        assert row["state"] == "draft"
        assert not row["executable"]
        assert row["can_run_once"]
        detail = (
            await (await client.get(f"/skills/{row['slug']}", headers=headers)).json()
        )["skill"]
        assert "Research {subject}" in detail["markdown"]
        assert detail["versions"] == [1]
        assert (
            await client.delete(f"/skills/{row['slug']}", headers=headers)
        ).status == 200
        assert (await (await client.get("/skills", headers=headers)).json())[
            "skills"
        ] == []
        assert (
            await client.post(f"/skills/{row['slug']}/restore", headers=headers)
        ).status == 200
        restored = (
            await (await client.get(f"/skills/{row['slug']}", headers=headers)).json()
        )["skill"]
        assert restored["state"] == "draft"


async def test_bridge_shows_loaded_and_verified_procedure_history(tmp_path):
    from task_routing import decide_task

    catalog = SkillCatalog(tmp_path / "skills")
    catalog.create_skill("research-topic", RESEARCH, category="web")
    info = catalog.get_skill("research-topic")
    decision = decide_task(
        request(),
        catalog,
        selected=info.canonical_id,
        explicit=True,
        available_tools=("browse_to", "read_page"),
    )
    catalog.state_store.load_run("loaded-one", info, decision)
    app = create_app(
        BridgeConfig("", "", "", "test-secret"),
        tasks=TaskStore(tmp_path / "tasks.db"),
        skills=SkillStore(tmp_path / "workflows"),
        skills_catalog=catalog,
    )
    async with TestClient(TestServer(app)) as client:
        headers = {SHARED_SECRET_HEADER: "test-secret"}
        rows = (await (await client.get("/skills/history", headers=headers)).json())[
            "runs"
        ]
        assert rows[0]["status"] == "loaded" and not rows[0]["verified"]
        assert rows[0]["request_id"] == "request-one"
        assert "Microsoft" not in str(rows)
