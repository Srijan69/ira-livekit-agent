"""Local HTTP bridge between the desktop dashboard and LiveKit.

Runs on loopback only, started and supervised by the Electron main process.
It handles join tokens, task records and configuration health. Conversation
itself goes over the LiveKit room, not through here.

Two design rules this file follows:

- **The API secret never leaves this process.** The renderer only ever sees a
  signed JWT.
- **Fail closed.** A missing shared secret rejects every request rather than
  disabling authentication, and absent LiveKit credentials produce a 503 that
  names the variable instead of a confusing 500.

Run it directly for debugging; in normal use Electron spawns it with a port
and a per-launch secret:

    uv run python src/bridge.py --port 8787
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import secrets
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from aiohttp import web
from dotenv import load_dotenv
from livekit.api import AccessToken, RoomAgentDispatch, RoomConfiguration, VideoGrants

from personal_memory import PersonalMemoryStore
from personal_memory_bridge import register_personal_memory_routes
from skill_bridge import register_skill_routes
from skill_store import SkillStore
from tasks import Task, TaskStore

logger = logging.getLogger("bridge")

#: Typed key, so aiohttp can check what we stash on the app.
CONFIG = web.AppKey("config", "BridgeConfig")
TASKS = web.AppKey("tasks", TaskStore)

load_dotenv(".env.local")

#: Header carrying the per-launch secret. Loopback binding keeps the network
#: out but not other local processes, and a token here is the ability to talk
#: to IRA -- which is the ability to issue desktop commands.
SHARED_SECRET_HEADER = "X-IRA-Bridge-Secret"

#: Must match `agent_name` in src/agent.py, or dispatch names an agent that
#: does not exist and nobody joins the room.
DEFAULT_AGENT_NAME = "my-agent"

#: Short enough to limit a leaked token, long enough to outlast a conversation.
TOKEN_TTL = timedelta(hours=1)

#: Room names are attacker-controllable over loopback, and end up in a signed
#: grant. Keep them short and bounded.
MAX_ROOM_NAME = 120
MAX_IDENTITY = 120


@dataclass(frozen=True)
class BridgeConfig:
    livekit_url: str
    api_key: str
    api_secret: str
    shared_secret: str
    agent_name: str = DEFAULT_AGENT_NAME
    google_api_key: str = field(default="present-by-default")

    @classmethod
    def from_env(cls, shared_secret: str) -> BridgeConfig:
        return cls(
            livekit_url=os.getenv("LIVEKIT_URL", ""),
            api_key=os.getenv("LIVEKIT_API_KEY", ""),
            api_secret=os.getenv("LIVEKIT_API_SECRET", ""),
            google_api_key=os.getenv("GOOGLE_API_KEY", ""),
            agent_name=os.getenv("IRA_AGENT_NAME", DEFAULT_AGENT_NAME),
            shared_secret=shared_secret,
        )

    def missing_credential(self) -> str | None:
        """Name the first absent LiveKit variable, for a useful error."""
        for name, value in (
            ("LIVEKIT_URL", self.livekit_url),
            ("LIVEKIT_API_KEY", self.api_key),
            ("LIVEKIT_API_SECRET", self.api_secret),
        ):
            if not value:
                return name
        return None


def _require_secret(request: web.Request) -> None:
    config: BridgeConfig = request.app[CONFIG]
    presented = request.headers.get(SHARED_SECRET_HEADER, "")

    # An empty configured secret rejects everything. A misconfiguration that
    # silently turns authentication off is worse than one that stops the app.
    if not config.shared_secret or not secrets.compare_digest(
        presented, config.shared_secret
    ):
        raise web.HTTPUnauthorized(
            text='{"error":"bad or missing bridge secret"}',
            content_type="application/json",
        )


def _bounded(value: object, limit: int, label: str) -> str:
    text = "" if value is None else str(value)
    if len(text) > limit:
        raise web.HTTPBadRequest(
            text=f'{{"error":"{label} is too long"}}',
            content_type="application/json",
        )
    return text


async def handle_token(request: web.Request) -> web.Response:
    """Mint a room token with IRA's dispatch baked in.

    The agent is on explicit dispatch, so it will not join a room the
    dashboard creates. Embedding a RoomConfiguration in the token makes the
    client's own join the thing that summons her -- one call, and no window
    where the user is in a room alone because a separate dispatch request
    hasn't landed yet.
    """
    _require_secret(request)
    config: BridgeConfig = request.app[CONFIG]

    if (missing := config.missing_credential()) is not None:
        return web.json_response(
            {"error": f"{missing} is not set; add it to .env.local"}, status=503
        )

    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    room = _bounded(payload.get("room"), MAX_ROOM_NAME, "room") or (
        f"ira-{secrets.token_hex(6)}"
    )
    identity = _bounded(payload.get("identity"), MAX_IDENTITY, "identity") or "boss"

    token = (
        AccessToken(config.api_key, config.api_secret)
        .with_identity(identity)
        .with_name(identity)
        .with_ttl(TOKEN_TTL)
        .with_grants(
            VideoGrants(
                room_join=True,
                room=room,
                can_publish=True,
                can_subscribe=True,
                # Carries the UI's typed commands on the lk.chat topic.
                can_publish_data=True,
            )
        )
        .with_room_config(
            RoomConfiguration(agents=[RoomAgentDispatch(agent_name=config.agent_name)])
        )
    )

    return web.json_response(
        {"url": config.livekit_url, "room": room, "token": token.to_jwt()}
    )


async def handle_health(request: web.Request) -> web.Response:
    """Report which configuration is present, as booleans only.

    Deliberately reachable without the shared secret: its entire job is to
    explain why nothing works, and that has to include a wrong secret. It
    returns whether a key is set and never what it is.
    """
    config: BridgeConfig = request.app[CONFIG]
    return web.json_response(
        {
            "bridge": "ok",
            "config": {
                "livekit_url": bool(config.livekit_url),
                "api_key": bool(config.api_key),
                "api_secret": bool(config.api_secret),
                "google_api_key": bool(config.google_api_key),
            },
        }
    )


async def _task_payload(request: web.Request) -> dict:
    try:
        payload = await request.json()
    except (ValueError, UnicodeError) as exc:
        raise web.HTTPBadRequest(
            text=json.dumps({"error": "body must be a JSON object"}),
            content_type="application/json",
        ) from exc
    if not isinstance(payload, dict):
        raise web.HTTPBadRequest(
            text=json.dumps({"error": "body must be a JSON object"}),
            content_type="application/json",
        )
    return payload


def _task_json(task: Task) -> dict:
    return {
        "id": str(task.id),
        "title": task.title,
        "due_at": task.due_at,
        "done": task.done,
        "created_at": task.created_at,
    }


async def handle_tasks_list(request: web.Request) -> web.Response:
    _require_secret(request)
    try:
        limit = int(request.query.get("limit", "8"))
        tasks = await asyncio.to_thread(request.app[TASKS].upcoming, limit=limit)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    return web.json_response({"tasks": [_task_json(task) for task in tasks]})


async def handle_tasks_create(request: web.Request) -> web.Response:
    _require_secret(request)
    payload = await _task_payload(request)
    try:
        task = await asyncio.to_thread(
            request.app[TASKS].add, payload.get("title"), payload.get("due_at")
        )
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    return web.json_response({"task": _task_json(task)}, status=201)


async def handle_tasks_update(request: web.Request) -> web.Response:
    _require_secret(request)
    payload = await _task_payload(request)
    try:
        task_id = int(request.match_info["id"])
        task = await asyncio.to_thread(
            request.app[TASKS].set_done, task_id, payload.get("done")
        )
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    if task is None:
        return web.json_response({"error": "task not found"}, status=404)
    return web.json_response({"task": _task_json(task)})


async def handle_tasks_delete(request: web.Request) -> web.Response:
    _require_secret(request)
    try:
        task_id = int(request.match_info["id"])
        deleted = await asyncio.to_thread(request.app[TASKS].delete, task_id)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    if not deleted:
        return web.json_response({"error": "task not found"}, status=404)
    return web.json_response({"deleted": True})


async def _close_tasks(app: web.Application) -> None:
    await asyncio.to_thread(app[TASKS].close)


def create_app(
    config: BridgeConfig,
    *,
    tasks: TaskStore | None = None,
    skills: SkillStore | None = None,
    run_path: str | Path | None = None,
    personal_memory: PersonalMemoryStore | None = None,
) -> web.Application:
    """Build the bridge; the app closes its injected or default store on cleanup."""
    app = web.Application()
    app[CONFIG] = config
    app[TASKS] = tasks if tasks is not None else TaskStore()
    app.on_cleanup.append(_close_tasks)
    register_skill_routes(app, _require_secret, skills=skills, run_path=run_path)
    register_personal_memory_routes(app, _require_secret, store=personal_memory)
    app.add_routes(
        [
            web.post("/token", handle_token),
            web.get("/health", handle_health),
            web.get("/tasks", handle_tasks_list),
            web.post("/tasks", handle_tasks_create),
            web.patch("/tasks/{id}", handle_tasks_update),
            web.delete("/tasks/{id}", handle_tasks_delete),
        ]
    )
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="IRA dashboard bridge")
    # Electron picks a free port by bind-testing in Node and passes it here, so
    # it knows the port authoritatively rather than parsing our stdout.
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    # The secret comes from the parent process. Generating one here would mean
    # the renderer could never learn it.
    shared_secret = os.getenv("IRA_BRIDGE_SECRET", "")
    if not shared_secret:
        raise SystemExit(
            "IRA_BRIDGE_SECRET is not set. The Electron main process normally "
            "generates it per launch and passes it in; export one by hand to "
            "run the bridge standalone."
        )

    logging.basicConfig(level=logging.INFO)
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        # Binding beyond loopback would expose token minting to the network.
        raise SystemExit("the bridge binds to loopback only")

    # The dashboard treats this line as "ready", so it is printed by run_app
    # once the socket is actually bound -- logging it beforehand would claim
    # readiness a few milliseconds before it was true.
    web.run_app(
        create_app(BridgeConfig.from_env(shared_secret)),
        host=args.host,
        port=args.port,
        print=lambda _banner: logger.info(
            "bridge listening on http://%s:%s", args.host, args.port
        ),
    )


if __name__ == "__main__":
    main()
