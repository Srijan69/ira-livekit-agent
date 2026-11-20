"""Authenticated skill management; execution belongs to the voice agent."""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import re
from collections.abc import Callable
from pathlib import Path

from aiohttp import web

from curator import SkillCurator
from skill_runs import SkillRunStore
from skill_store import SkillStore, StoredSkill
from skills import SkillCatalog

SKILLS = web.AppKey("skills", object)
RUN_PATH = web.AppKey("skill_run_path", object)
PROCEDURES = web.AppKey("procedural_skills", object)


def _procedures(app):
    existing = app[PROCEDURES]
    return existing if existing is not None else SkillCatalog()


def _procedure_summary(catalog, info):
    relative = info.path.parent.relative_to(catalog.root).as_posix()
    return {
        "slug": "procedure-" + hashlib.sha256(relative.encode()).hexdigest()[:20],
        "name": info.name,
        "description": info.description,
        "version": info.version,
        "source": "package",
        "state": "ready",
        "mode": "procedure",
        "procedure_name": "procedure:" + relative,
        "category": info.category,
        "pinned": False,
        "archived": False,
        "use_count": 0,
        "failure_count": 0,
        "issues": [],
        "executable": True,
    }


def _procedure_entry(app, slug):
    catalog = _procedures(app)
    for info in catalog.list_skills():
        result = _procedure_summary(catalog, info)
        if result["slug"] == slug:
            return info, result
    return None


def _summary(entry: StoredSkill) -> dict:
    skill = entry.skill
    return {
        "slug": entry.slug,
        "mode": "compiled",
        "name": skill.name if skill else entry.slug,
        "description": skill.description if skill else "This skill needs repair.",
        "version": skill.version if skill else 0,
        "source": skill.source if skill else "edited",
        "state": skill.state if skill else "draft",
        "pinned": skill.pinned if skill else False,
        "archived": entry.archived,
        "use_count": entry.use_count,
        "failure_count": entry.failure_count,
        "issues": [str(issue) for issue in entry.issues],
        "executable": bool(
            skill
            and skill.state == "enabled"
            and not entry.archived
            and not entry.issues
        ),
    }


def _slug(request: web.Request) -> str:
    slug = request.match_info["slug"]
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,127}", slug):
        raise ValueError("Invalid skill slug")
    return slug


def _store(app: web.Application) -> SkillStore:
    # Called within to_thread, after authentication. A default store is not
    # created by health/token requests or unauthenticated probes.
    existing = app[SKILLS]
    return existing if existing is not None else SkillStore()


def _entry(store: SkillStore, slug: str) -> StoredSkill:
    entry = next(
        (item for item in store.list(include_archived=True) if item.slug == slug), None
    )
    if entry is None:
        raise KeyError("Skill not found")
    return entry


def register_skill_routes(
    app: web.Application,
    require_secret: Callable[[web.Request], None],
    *,
    skills: SkillStore | None = None,
    skills_catalog: SkillCatalog | None = None,
    run_path: str | Path | None = None,
) -> None:
    app[SKILLS] = skills
    app[RUN_PATH] = run_path
    app[PROCEDURES] = skills_catalog

    async def response(request, operation):
        require_secret(request)
        try:
            return web.json_response(await asyncio.to_thread(operation))
        except (ValueError, TypeError) as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except KeyError:
            return web.json_response({"error": "Skill not found"}, status=404)
        except OSError:
            return web.json_response(
                {"error": "Skill storage unavailable. Try again."}, status=503
            )

    async def catalog(request):
        def read():
            include = request.query.get("include_archived", "false")
            if include not in {"true", "false"}:
                raise ValueError("include_archived must be true or false")
            procedures = _procedures(app)
            return {
                "skills": [
                    _summary(entry)
                    for entry in _store(app).list(include_archived=include == "true")
                ]
                + [
                    _procedure_summary(procedures, info)
                    for info in procedures.list_skills()
                ]
            }

        return await response(request, read)

    async def detail(request):
        def read():
            procedure = _procedure_entry(app, _slug(request))
            if procedure:
                if "version" in request.query:
                    raise ValueError(
                        "Procedural packages expose their current document; version history applies to compiled workflows."
                    )
                info, result = procedure
                result.update(
                    markdown=info.path.read_text(encoding="utf-8"),
                    versions=[],
                    linked_files=info.linked_files,
                )
                return {"skill": result}
            store = _store(app)
            entry = _entry(store, _slug(request))
            result = _summary(entry)
            latest = (
                store.version_text(entry.slug, entry.skill.version)
                if entry.skill
                else ""
            )
            selected = int(request.query.get("version", str(result["version"])))
            if selected < 1 and entry.skill:
                raise ValueError("version must be positive")
            markdown = store.version_text(entry.slug, selected) if selected > 0 else ""
            if markdown is None:
                raise KeyError("Version not found")
            result["selected_version"] = selected
            result["markdown"] = markdown
            result["diff"] = (
                "".join(
                    difflib.unified_diff(
                        markdown.splitlines(keepends=True),
                        (latest or "").splitlines(keepends=True),
                        fromfile=f"version-{selected}",
                        tofile=f"version-{result['version']}",
                    )
                )
                if selected != result["version"]
                else ""
            )
            result["versions"] = store.versions(entry.slug)
            return {"skill": result}

        return await response(request, read)

    async def history(request):
        def read():
            limit = int(request.query.get("limit", "50"))
            if not 1 <= limit <= 100:
                raise ValueError("limit must be between 1 and 100")
            store = _store(app)
            runs = SkillRunStore(app[RUN_PATH] or store.root / ".runs.sqlite3")
            return {"runs": runs.list(limit=limit)}

        return await response(request, read)

    async def suggestions(request):
        return await response(
            request, lambda: {"suggestions": SkillCurator(_store(app)).suggest()}
        )

    async def pin(request):
        require_secret(request)
        try:
            payload = await request.json()
        except (ValueError, UnicodeError):
            payload = None
        if (
            not isinstance(payload, dict)
            or set(payload) != {"pinned"}
            or not isinstance(payload["pinned"], bool)
        ):
            return web.json_response(
                {"error": "body must contain only a boolean pinned field"}, status=400
            )

        def update():
            if _procedure_entry(app, _slug(request)):
                raise ValueError(
                    "Use IRA's skill_manage to edit procedural packages. Pinning applies to compiled workflows."
                )
            store = _store(app)
            slug = _slug(request)
            _entry(store, slug)
            if store.set_pinned(slug, payload["pinned"]) is None:
                raise ValueError("Repair the skill before changing its pin")
            return {"skill": _summary(_entry(store, slug))}

        return await response(request, update)

    async def archive(request):
        def update():
            if _procedure_entry(app, _slug(request)):
                raise ValueError(
                    "Use IRA's skill_manage to manage procedural packages. Archiving applies to compiled workflows."
                )
            store = _store(app)
            slug = _slug(request)
            _entry(store, slug)
            store.archive(slug)
            return {"archived": True}

        return await response(request, update)

    async def restore(request):
        def update():
            store = _store(app)
            slug = _slug(request)
            if store.restore(slug) is None:
                raise KeyError(slug)
            return {"skill": _summary(_entry(store, slug))}

        return await response(request, update)

    app.add_routes(
        [
            web.get("/skills", catalog),
            web.get("/skills/history", history),
            web.get("/skills/suggestions", suggestions),
            web.get("/skills/{slug}", detail),
            web.patch("/skills/{slug}", pin),
            web.delete("/skills/{slug}", archive),
            web.post("/skills/{slug}/restore", restore),
        ]
    )
