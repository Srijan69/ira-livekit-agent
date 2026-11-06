import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personal_memory import PersonalMemoryStore
from personal_memory_bridge import register_personal_memory_routes

AUTH = {"X-IRA-Bridge-Secret": "synthetic-secret"}


@pytest.fixture
def store(tmp_path):
    instance = PersonalMemoryStore(tmp_path / "personal.sqlite3")
    yield instance
    instance.close()


@pytest.fixture
async def client(store):
    app = web.Application()

    def authorize(request):
        if request.headers.get("X-IRA-Bridge-Secret") != "synthetic-secret":
            raise web.HTTPUnauthorized(
                text=json.dumps({"error": "Invalid secret"}),
                content_type="application/json",
            )

    register_personal_memory_routes(app, authorize, store=store)
    async with TestClient(TestServer(app)) as instance:
        yield instance


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", "/profile", None),
        ("PATCH", "/profile", {"name": "Avery"}),
        ("POST", "/profile/reset", {}),
        ("GET", "/memory/settings", None),
        ("PATCH", "/memory/settings", {"enabled": False}),
        ("GET", "/memory", None),
        ("GET", "/memory/status", None),
        ("DELETE", "/memory/any", None),
        ("POST", "/memory/clear", {}),
    ],
)
@pytest.mark.parametrize("secret", [None, "wrong"])
async def test_all_routes_authenticate_before_opening_storage(
    client, tmp_path, method, path, body, secret
):
    headers = {} if secret is None else {"X-IRA-Bridge-Secret": secret}
    response = await client.request(method, path, json=body, headers=headers)
    assert response.status == 401
    assert not (tmp_path / "personal.sqlite3").exists()


async def test_profile_read_save_reset_and_revision_conflict(client):
    response = await client.get("/profile", headers=AUTH)
    assert (await response.json())["name"] == ""
    response = await client.patch(
        "/profile",
        json={"name": "Avery", "age": 31, "expected_revision": 0},
        headers=AUTH,
    )
    saved = await response.json()
    assert response.status == 200
    assert saved["revision"] == 1
    response = await client.patch(
        "/profile", json={"name": "Jordan", "expected_revision": 0}, headers=AUTH
    )
    assert response.status == 409
    response = await client.post(
        "/profile/reset", json={"expected_revision": 1}, headers=AUTH
    )
    cleared = await response.json()
    assert response.status == 200
    assert cleared["name"] == ""
    assert cleared["age"] is None
    assert cleared["revision"] == 2


@pytest.mark.parametrize(
    "path,method,payload",
    [
        ("/profile", "PATCH", []),
        ("/profile", "PATCH", {"age": True}),
        ("/profile", "PATCH", {"expected_revision": True}),
        ("/profile", "PATCH", {"source": "summary"}),
        ("/profile/reset", "POST", {"name": "Avery"}),
        ("/memory/settings", "PATCH", {"enabled": "yes"}),
        ("/memory/settings", "PATCH", {"summary_trigger": 99999}),
        ("/memory/clear", "POST", {"profile": True}),
    ],
)
async def test_mutations_reject_invalid_payloads_without_writing(
    client, store, path, method, payload
):
    response = await client.request(method, path, json=payload, headers=AUTH)
    assert response.status == 400
    assert (await response.json())["error"]
    assert store.get_profile()["revision"] == 0
    assert store.get_settings()["revision"] == 0


@pytest.mark.parametrize(
    "path,method",
    [
        ("/profile", "PATCH"),
        ("/profile/reset", "POST"),
        ("/memory/settings", "PATCH"),
        ("/memory/clear", "POST"),
    ],
)
@pytest.mark.parametrize("body", ["null", "[", "not-json"])
async def test_mutations_reject_malformed_json(client, path, method, body):
    response = await client.request(method, path, data=body, headers=AUTH)
    assert response.status == 400


async def test_memory_inspect_delete_clear_and_status_share_store(client, store):
    store.save_profile({"name": "Avery"})
    first = store.add_fact("Garden has roses")
    store.add_fact("A different workspace")
    response = await client.get("/memory?query=roses&limit=1", headers=AUTH)
    assert (await response.json())["memories"] == [first]
    response = await client.delete(f"/memory/{first['id']}", headers=AUTH)
    assert await response.json() == {"deleted": True}
    response = await client.delete(f"/memory/{first['id']}", headers=AUTH)
    assert response.status == 404
    response = await client.get("/memory/status", headers=AUTH)
    assert (await response.json())["memory_revision"] == 4
    response = await client.post("/memory/clear", json={}, headers=AUTH)
    assert await response.json() == {"cleared": True}
    assert store.list_memories() == []
    assert store.get_profile()["name"] == "Avery"


async def test_settings_save_and_conflict(client):
    response = await client.patch(
        "/memory/settings",
        json={"enabled": False, "expected_revision": 0},
        headers=AUTH,
    )
    assert response.status == 200
    assert (await response.json())["enabled"] is False
    response = await client.patch(
        "/memory/settings", json={"enabled": True, "expected_revision": 0}, headers=AUTH
    )
    assert response.status == 409


@pytest.mark.parametrize(
    "query",
    ["limit=0", "limit=101", "limit=1.5", "limit=true", "limit=", "query=" + "x" * 401],
)
async def test_memory_list_rejects_unbounded_or_malformed_query(client, query):
    response = await client.get("/memory?" + query, headers=AUTH)
    assert response.status == 400
