"""Real app composition with isolated SQLite stores and synthetic credentials."""

from aiohttp.test_utils import TestClient, TestServer

from bridge import BridgeConfig, create_app
from personal_memory import PersonalMemoryStore
from tasks import TaskStore


async def test_profile_persists_across_bridge_restarts_without_any_provider_keys(
    tmp_path,
):
    path = tmp_path / "personal.sqlite3"
    config = BridgeConfig(
        livekit_url="", api_key="", api_secret="", shared_secret="synthetic"
    )
    headers = {"X-IRA-Bridge-Secret": "synthetic"}
    app = create_app(
        config, tasks=TaskStore(":memory:"), personal_memory=PersonalMemoryStore(path)
    )
    async with TestClient(TestServer(app)) as client:
        assert (await client.get("/profile")).status == 401
        response = await client.patch(
            "/profile",
            json={"name": "Alex", "age": 30, "expected_revision": 0},
            headers=headers,
        )
        assert response.status == 200
    app = create_app(
        config, tasks=TaskStore(":memory:"), personal_memory=PersonalMemoryStore(path)
    )
    async with TestClient(TestServer(app)) as client:
        body = await (await client.get("/profile", headers=headers)).json()
        assert body["name"] == "Alex" and body["age"] == 30
        assert (
            await client.post("/profile/reset", json={}, headers=headers)
        ).status == 200
    store = PersonalMemoryStore(path)
    assert store.get_profile()["name"] == "" and store.get_profile()["age"] is None
    store.close()
