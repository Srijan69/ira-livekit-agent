"""Authenticated task HTTP contracts use only loopback and temporary SQLite."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bridge import SHARED_SECRET_HEADER, BridgeConfig, create_app
from tasks import TaskStore

AUTH = {SHARED_SECRET_HEADER: "test-bridge-secret"}


def _config(shared_secret="test-bridge-secret"):
    # Task panels must work even when no LiveKit credentials are configured.
    return BridgeConfig(
        livekit_url="", api_key="", api_secret="", shared_secret=shared_secret
    )


@pytest.fixture
def store(tmp_path):
    instance = TaskStore(tmp_path / "tasks.db")
    yield instance
    instance.close()


@pytest.fixture
async def client(store):
    async with TestClient(TestServer(create_app(_config(), tasks=store))) as client:
        yield client


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/tasks", None),
        ("POST", "/tasks", {"title": "Private task"}),
        ("PATCH", "/tasks/1", {"done": True}),
        ("DELETE", "/tasks/1", None),
    ],
)
@pytest.mark.parametrize("secret", [None, "wrong-secret"])
async def test_every_task_operation_rejects_missing_or_wrong_secret(
    client, tmp_path, method, path, body, secret
):
    headers = {} if secret is None else {SHARED_SECRET_HEADER: secret}
    response = await client.request(method, path, json=body, headers=headers)
    assert response.status == 401
    assert "secret" in (await response.json())["error"]
    assert not (tmp_path / "tasks.db").exists()


@pytest.mark.parametrize("method", ["GET", "POST", "PATCH", "DELETE"])
async def test_empty_configured_secret_rejects_task_operations(store, method):
    path = "/tasks" if method in {"GET", "POST"} else "/tasks/1"
    async with TestClient(TestServer(create_app(_config(""), tasks=store))) as client:
        response = await client.request(method, path, headers=AUTH)
        assert response.status == 401


async def test_create_returns_task_with_string_id_and_utc_timestamps(client, store):
    response = await client.post(
        "/tasks",
        json={"title": "  Call\n Alice ", "due_at": "2026-10-16T09:00:00+05:30"},
        headers=AUTH,
    )
    assert response.status == 201
    task = (await response.json())["task"]
    assert task == {
        "id": "1",
        "title": "Call Alice",
        "due_at": "2026-10-16T03:30:00Z",
        "done": False,
        "created_at": task["created_at"],
    }
    assert task["created_at"].endswith("Z")
    assert (
        datetime.fromisoformat(task["created_at"].replace("Z", "+00:00")).tzinfo
        == timezone.utc
    )
    assert store.upcoming()[0].title == "Call Alice"


@pytest.mark.parametrize(
    "payload", [{"title": "No date"}, {"title": "No date", "due_at": None}]
)
async def test_create_accepts_no_due_date(client, payload):
    response = await client.post("/tasks", json=payload, headers=AUTH)
    assert response.status == 201
    assert (await response.json())["task"]["due_at"] is None


async def test_list_returns_upcoming_records_from_injected_store(client, store):
    store.add("No date")
    store.add("Later", "2026-10-18T12:00:00Z")
    earliest = store.add("Soon", "2026-10-16T12:00:00Z")
    completed = store.add("Complete", "2026-10-15T12:00:00Z")
    store.set_done(completed.id, True)
    response = await client.get("/tasks?limit=1", headers=AUTH)
    assert response.status == 200
    tasks = (await response.json())["tasks"]
    assert [task["id"] for task in tasks] == [str(earliest.id)]
    assert tasks[0]["done"] is False


async def test_list_defaults_to_eight_and_empty_store_returns_empty_list(client, store):
    response = await client.get("/tasks", headers=AUTH)
    assert await response.json() == {"tasks": []}
    for number in range(10):
        store.add(f"Task {number}")
    response = await client.get("/tasks", headers=AUTH)
    assert len((await response.json())["tasks"]) == 8


@pytest.mark.parametrize(
    "limit", ["", "0", "-1", "101", "1.5", "true", "many", "9999999999999999999999999"]
)
async def test_list_rejects_invalid_limit(client, limit):
    response = await client.get("/tasks", params={"limit": limit}, headers=AUTH)
    assert response.status == 400
    assert (await response.json())["error"]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"title": ""},
        {"title": " \n "},
        {"title": "x" * 501},
        {"title": None},
        {"title": 10},
        {"title": True},
        {"title": []},
        {"title": "Call", "due_at": "2026-10-16T09:00:00"},
        {"title": "Call", "due_at": "2026-10-16"},
        {"title": "Call", "due_at": "tomorrow"},
        {"title": "Call", "due_at": "2026-02-30T09:00:00Z"},
        {"title": "Call", "due_at": 123},
        {"title": "Call", "due_at": False},
        {"title": "Call", "due_at": {}},
        [],
        "a task",
        1,
    ],
)
async def test_create_rejects_invalid_payload_without_writing(client, store, payload):
    response = await client.post("/tasks", json=payload, headers=AUTH)
    assert response.status == 400
    assert (await response.json())["error"]
    assert store.upcoming() == []


@pytest.mark.parametrize("method", ["POST", "PATCH"])
@pytest.mark.parametrize("body", ["not JSON", "null", "["])
async def test_rejects_malformed_json(client, method, body):
    path = "/tasks" if method == "POST" else "/tasks/1"
    response = await client.request(method, path, data=body, headers=AUTH)
    assert response.status == 400
    assert (await response.json())["error"]


async def test_completion_can_be_toggled_and_persists_after_store_reopens(
    client, store
):
    original = store.add("Finish review")
    response = await client.patch(
        f"/tasks/{original.id}", json={"done": True}, headers=AUTH
    )
    assert response.status == 200
    task = (await response.json())["task"]
    assert task["id"] == str(original.id)
    assert task["done"] is True
    store.close()
    assert store.upcoming() == []
    response = await client.patch(
        f"/tasks/{original.id}", json={"done": False}, headers=AUTH
    )
    assert response.status == 200
    assert (await response.json())["task"]["done"] is False
    assert store.upcoming() == [original]


@pytest.mark.parametrize(
    "payload",
    [{}, {"done": "false"}, {"done": 1}, {"done": 0}, {"done": None}, {"done": []}, []],
)
async def test_patch_requires_boolean_done(client, store, payload):
    task = store.add("Keep unfinished")
    response = await client.patch(f"/tasks/{task.id}", json=payload, headers=AUTH)
    assert response.status == 400
    assert (await response.json())["error"]
    assert store.upcoming() == [task]


async def test_delete_removes_record_and_reports_missing_on_second_delete(
    client, store
):
    task = store.add("Remove me")
    response = await client.delete(f"/tasks/{task.id}", headers=AUTH)
    assert response.status == 200
    assert await response.json() == {"deleted": True}
    assert store.upcoming() == []
    response = await client.delete(f"/tasks/{task.id}", headers=AUTH)
    assert response.status == 404
    assert (await response.json())["error"]


@pytest.mark.parametrize("method", ["PATCH", "DELETE"])
async def test_unknown_ids_return_404(client, method):
    response = await client.request(
        method, "/tasks/999", json={"done": True}, headers=AUTH
    )
    assert response.status == 404
    assert (await response.json())["error"]


@pytest.mark.parametrize("method", ["PATCH", "DELETE"])
@pytest.mark.parametrize(
    "task_id", ["0", "-1", "1.5", "true", "nope", "9223372036854775808"]
)
async def test_invalid_ids_return_400(client, method, task_id):
    response = await client.request(
        method, f"/tasks/{task_id}", json={"done": True}, headers=AUTH
    )
    assert response.status == 400
    assert (await response.json())["error"]


async def test_app_cleanup_closes_its_store(store):
    conn = store.connect()
    async with TestClient(TestServer(create_app(_config(), tasks=store))) as client:
        response = await client.get("/tasks", headers=AUTH)
        assert response.status == 200
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")
