from aiohttp.test_utils import TestClient, TestServer

from bridge import SHARED_SECRET_HEADER, BridgeConfig, create_app
from skill_store import SkillStore
from skills import SkillCatalog
from tasks import TaskStore


async def test_dashboard_catalog_and_detail_use_same_procedural_library(
    tmp_path, monkeypatch
):
    catalog = SkillCatalog(tmp_path / "skills")
    catalog.create_skill(
        "play-spotify", "Call spotify_play_track; verify the result.", category="media"
    )
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
        assert row["procedure_name"] == "procedure:media/play-spotify"
        assert row["state"] == "ready"
        detail = (
            await (await client.get(f"/skills/{row['slug']}", headers=headers)).json()
        )["skill"]
        assert "spotify_play_track" in detail["markdown"]
        assert detail["versions"] == []
        assert (
            await client.delete(f"/skills/{row['slug']}", headers=headers)
        ).status == 400
