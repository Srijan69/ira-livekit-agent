"""Authenticated personal-memory routes; SQLite work stays off the event loop."""

from __future__ import annotations

import asyncio
import sqlite3

from aiohttp import web

from personal_memory import MemoryConflict, PersonalMemoryStore

PERSONAL_MEMORY = web.AppKey("personal_memory", PersonalMemoryStore)


def register_personal_memory_routes(app, authorize, *, store=None):
    app[PERSONAL_MEMORY] = store if store is not None else PersonalMemoryStore()

    async def response(request, operation):
        authorize(request)
        try:
            return web.json_response(await asyncio.to_thread(operation))
        except MemoryConflict as exc:
            return web.json_response({"error": str(exc)}, status=409)
        except (ValueError, TypeError) as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except KeyError:
            return web.json_response({"error": "Memory not found"}, status=404)
        except (OSError, sqlite3.Error):
            return web.json_response(
                {"error": "Memory storage unavailable. Try again."}, status=503
            )

    async def payload(request):
        authorize(request)
        try:
            body = await request.json()
        except (ValueError, UnicodeError) as exc:
            raise ValueError("body must contain valid JSON") from exc
        if not isinstance(body, dict):
            raise ValueError("body must be a JSON object")
        return body

    async def mutate(request, operation):
        authorize(request)
        try:
            body = await payload(request)
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return await response(request, lambda: operation(body))

    async def profile_get(request):
        return await response(request, app[PERSONAL_MEMORY].get_profile)

    async def profile_patch(request):
        def save(body):
            expected = body.pop("expected_revision", None)
            return app[PERSONAL_MEMORY].save_profile(body, expected_revision=expected)

        return await mutate(request, save)

    async def profile_reset(request):
        def reset(body):
            if set(body) - {"expected_revision"}:
                raise ValueError("reset accepts only expected_revision")
            return app[PERSONAL_MEMORY].save_profile(
                {"name": "", "age": None, "occupation": "", "preferences": ""},
                expected_revision=body.get("expected_revision"),
            )

        return await mutate(request, reset)

    async def settings_get(request):
        return await response(request, app[PERSONAL_MEMORY].get_settings)

    async def settings_patch(request):
        def save(body):
            expected = body.pop("expected_revision", None)
            return app[PERSONAL_MEMORY].save_settings(body, expected_revision=expected)

        return await mutate(request, save)

    async def memories_get(request):
        def read():
            value = request.query.get("limit", "100")
            if not value.isascii() or not value.isdecimal():
                raise ValueError("limit must be an integer between 1 and 100")
            return {
                "memories": app[PERSONAL_MEMORY].list_memories(
                    request.query.get("query", ""), limit=int(value)
                )
            }

        return await response(request, read)

    async def status_get(request):
        return await response(request, app[PERSONAL_MEMORY].get_status)

    async def memory_delete(request):
        def delete():
            if not app[PERSONAL_MEMORY].delete_memory(request.match_info["id"]):
                raise KeyError("Memory not found")
            return {"deleted": True}

        return await response(request, delete)

    async def memory_clear(request):
        def clear(body):
            if body:
                raise ValueError("clear accepts an empty JSON object")
            app[PERSONAL_MEMORY].clear_conversation_memory()
            return {"cleared": True}

        return await mutate(request, clear)

    async def cleanup(application):
        await asyncio.to_thread(application[PERSONAL_MEMORY].close)

    app.on_cleanup.append(cleanup)
    app.add_routes(
        [
            web.get("/profile", profile_get),
            web.patch("/profile", profile_patch),
            web.post("/profile/reset", profile_reset),
            web.get("/memory/settings", settings_get),
            web.patch("/memory/settings", settings_patch),
            web.get("/memory", memories_get),
            web.get("/memory/status", status_get),
            web.delete("/memory/{id}", memory_delete),
            web.post("/memory/clear", memory_clear),
        ]
    )
