"""Thin LiveKit tools over the agent-owned execution service."""

import asyncio
import json
import logging
import re
import time
import uuid
from contextlib import suppress
from contextvars import copy_context
from dataclasses import replace
from threading import Lock

from livekit.agents import RunContext, ToolError, function_tool
from livekit.agents.llm import ToolFlag, find_function_tools

from host_execution import HostActionTimeout
from request_scope import ScopeViolation
from request_scope_livekit import check_current_action
from skill_adapters import SkillToolAdapters
from skill_execution import SkillExecutionService
from skill_format import parse_skill
from skill_runs import TERMINAL_STATUSES, SkillRunStore
from task_routing import decide_task, normalized_name

logger = logging.getLogger(__name__)

MODEL_OUTPUT_LIMIT = 2000


def _skill_json(fields: dict) -> str:
    """Bound prose while retaining complete JSON and exact correlation IDs."""
    fields = dict(fields)

    def encode():
        return json.dumps(fields, ensure_ascii=False, separators=(",", ":"))

    result = encode()
    for key in ("message", "question"):
        excess = len(result) - MODEL_OUTPUT_LIMIT
        if excess <= 0:
            break
        value = fields.get(key)
        if isinstance(value, str):
            # Each removed character occupies at least one encoded character.
            fields[key] = value[: max(0, len(value) - excess)]
            result = encode()
    if len(result) > MODEL_OUTPUT_LIMIT:
        raise ToolError("Skill status identifiers exceed the response limit.")
    return result


CAPABILITIES = {
    "browser": (
        "browse_to",
        "read_page",
        "list_page_elements",
        "click_element",
        "type_into_element",
        "go_back",
        "close_browser",
        "new_tab",
        "switch_tab",
        "list_tabs",
        "close_tab",
        "select_dropdown_option",
    ),
    "desktop": (
        "capture_screen",
        "inspect_screen",
        "focus_desktop_window",
        "move_mouse",
        "click_mouse",
        "click_desktop_element",
        "scroll_mouse",
        "drag_mouse",
        "type_on_desktop",
        "press_desktop_key",
    ),
    "skills": (
        "list_skills",
        "view_skill",
        "save_skill",
        "enable_skill",
        "undo_skill_change",
        "delete_skill",
        "learn_skill",
        "curate_skills",
        "skills_list",
        "skill_manage",
        "train_tool",
        "tool_mastery_status",
    ),
    "memory": (
        "remember",
        "recall",
        "forget",
        "save_note",
        "remember_preference",
        "list_preferences",
        "correct_memory",
        "memory",
    ),
    "market": (
        "generate_market_report",
        "create_styled_spreadsheet",
        "scrape_market_assets",
    ),
}


class SkillTools:
    _host_action_timeout_seconds = 30.0
    _host_shutdown_timeout_seconds = 1.0

    def _procedure_run_for(self, request_id):
        return getattr(self, "_procedure_runs", {}).get(request_id)

    def _settle_replaced_procedure(self, record):
        status = (
            "uncertain"
            if any(
                step.get("outcome") == "uncertain" for step in record.get("steps", ())
            )
            else "cancelled"
        )
        return self._skills_catalog.state_store.finish_run(
            record["run_id"],
            record["request_id"],
            status,
            summary="Procedure plan changed; continue the same task with a current plan.",
        )

    def validate_active_procedure(self, request_id, name):
        """Synchronous admission hook; changed packages require same-task replanning."""
        self._request_scope.require(request_id)
        run_id = self._procedure_run_for(request_id)
        if not run_id or name == "run_skill":
            return None
        store = self._skills_catalog.state_store
        record = store.run(run_id)
        if record is None or record["status"] in TERMINAL_STATUSES:
            return None
        try:
            info = self._skills_catalog.get_skill(
                record["skill_slug"], include_archived=True
            )
            state = store.state(info) if info else {}
            current = (
                info is not None
                and info.digest == record["document_digest"]
                and not state["archived"]
                and state["state"] in {"validated", "enabled"}
            )
        except (OSError, ValueError):
            current = False
        if current:
            return record
        self._settle_replaced_procedure(record)
        self._skills_changed()
        raise ScopeViolation(
            "The selected procedure changed or was archived. Replan this same active task using current supported tools; no new human approval is required.",
            status="procedure_replan_required",
            retryable=True,
        )

    async def record_procedure_action(
        self, request_id, name, outcome, evidence, *, action_id=""
    ):
        """Called by admitted tool settlement, never from a model success claim."""
        active = self._request_scope.current_request
        if active is None or active.id != request_id:
            raise ToolError("Procedure evidence requires the current task identity.")
        self._request_scope.require(request_id)
        run_id = self._procedure_run_for(request_id)
        if not run_id:
            return None
        store = self._skills_catalog.state_store
        record = await asyncio.to_thread(
            store.record_action,
            run_id,
            request_id,
            {
                "tool": name,
                "outcome": outcome,
                "evidence": evidence,
                "action_id": action_id,
            },
        )
        self._skills_changed()
        return record

    async def finish_procedure(
        self, request_id, verified, summary, *, criteria=None, status=None
    ):
        """Settle adaptive work only with host-verified current-goal criteria."""
        run_id = self._procedure_run_for(request_id)
        if not run_id:
            return None
        store = self._skills_catalog.state_store
        originating = store.run(run_id)
        if originating is None or originating["status"] in TERMINAL_STATUSES:
            return originating
        try:
            self._request_scope.require(request_id)
        except ScopeViolation:
            # Normal closure revokes action authority before its trusted outcome
            # callback runs. Its receipt permits history settlement only.
            if not self._request_scope.was_finished(request_id):
                result = self._settle_replaced_procedure(originating)
                self._skills_changed()
                return result
        else:
            self.validate_active_procedure(request_id, "finish_procedure")
        record = await asyncio.to_thread(
            store.finish_run,
            run_id,
            request_id,
            status or ("done" if verified else "error"),
            criteria=criteria,
            summary=summary,
        )
        self._skills_changed()
        return record

    async def cancel_procedures(self, request_id, *, status="cancelled"):
        """Trusted stop/supersession callback closes history without claiming success."""
        run_id = self._procedure_run_for(request_id)
        if not run_id:
            return None
        return await asyncio.to_thread(
            self._skills_catalog.state_store.finish_run, run_id, request_id, status
        )

    def _initial_capability_tools(self):
        """Gemini tools are immutable mid-session; install schemas before connect.

        Skill bodies and memory remain progressively retrieved. Pre-registering
        tools has no desktop/database effects and avoids restarting Live audio
        from within a capability-loading call.
        """
        names = {tool.info.name for tool in find_function_tools(self)}
        return [
            function_tool(getattr(self, name))
            for name in dict.fromkeys(
                name for group in CAPABILITIES.values() for name in group
            )
            if name not in names
        ]

    async def _get_skill_runtime(self):
        async with self._runtime_init_lock:
            return await asyncio.to_thread(self._ensure_skill_runtime)

    @function_tool
    async def load_capabilities(self, context: RunContext, category: str) -> str:
        """Inspect available tool groups: browser (read/click/type), desktop (capture/move/click/scroll/drag/type), skills (save/enable/learn/archive/undo), memory (preferences/corrections/forget), market (report/spreadsheet/scrape). Gemini schemas are already available at startup."""
        if category not in CAPABILITIES:
            raise ToolError(f"Choose {', '.join(CAPABILITIES.keys())}.")
        async with self._capability_lock:
            names = {tool.info.name for tool in self.tools}
            extra = [
                function_tool(getattr(self, name))
                for name in CAPABILITIES[category]
                if name not in names
            ]
            if extra:
                await self.update_tools([*self.tools, *extra])
        return f"{category} tools ready: {', '.join(CAPABILITIES[category])}."

    async def _run_host_action(self, fn, *args, _preflight=None, **kwargs):
        if getattr(self, "_host_closing", False):
            raise ToolError("Host session ended; await a new human request.")
        deadline = asyncio.get_running_loop().time() + self._host_action_timeout_seconds
        caller = asyncio.current_task()
        state_lock = Lock()
        abandoned = False
        started = False

        def invoke():
            nonlocal started
            # Executor submission can queue too. Decide start/abandon atomically,
            # immediately before native work, never from the async task's state.
            with state_lock:
                if (
                    abandoned
                    or caller.cancelling()
                    or getattr(self, "_host_closing", False)
                    or time.monotonic() >= deadline
                ):
                    raise HostActionTimeout(effect_started=False)
                started = True
            return fn(*args, **kwargs)

        async def owned_action():
            await self._execution_lock.acquire()
            worker = None
            try:
                if (
                    abandoned
                    or caller.cancelling()
                    or getattr(self, "_host_closing", False)
                    or asyncio.get_running_loop().time() >= deadline
                ):
                    raise HostActionTimeout(effect_started=False)
                check_current_action()
                computer = getattr(self, "_computer", None)
                if (
                    computer is not None
                    and getattr(fn, "__self__", None) is not computer
                ):
                    computer.invalidate()
                if _preflight is not None:
                    _preflight()
                check_current_action()
                # Submit immediately, with the same context propagation as
                # to_thread. A separate cancellable asyncio Task could settle
                # while its underlying native thread still runs.
                worker = asyncio.get_running_loop().run_in_executor(
                    None, copy_context().run, invoke
                )
                # Cancelling an async owner does not terminate its native thread.
                # Drain the worker before releasing, including repeated cancellation.
                while True:
                    try:
                        return await asyncio.shield(worker)
                    except asyncio.CancelledError:
                        if worker.done():
                            return worker.result()
            finally:
                self._execution_lock.release()

        job = asyncio.create_task(owned_action())
        self._host_jobs.add(job)

        def settled(task):
            self._host_jobs.discard(task)
            if not task.cancelled():
                task.exception()

        job.add_done_callback(settled)
        timeout = asyncio.timeout_at(deadline)
        try:
            async with timeout:
                return await asyncio.shield(job)
        except (TimeoutError, asyncio.CancelledError) as exc:
            # A TimeoutError raised by the worker is an ordinary worker failure.
            if isinstance(exc, TimeoutError) and not timeout.expired():
                raise
            with state_lock:
                abandoned = True
                effect_started = started
            job.cancel()
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise HostActionTimeout(effect_started=effect_started) from None

    def _ensure_skill_runtime(self):
        if self._skill_service is None:
            self._skill_adapters = SkillToolAdapters(
                self._browser,
                self._desktop,
                self._skill_launcher,
                before_action=self._computer.invalidate,
            )
            self._skill_registry = self._skill_adapters.registry()
            self._skill_service = SkillExecutionService(
                verifier=self._skill_adapters.verify,
                run_store=SkillRunStore(self._skills.root / ".runs.sqlite3"),
                execution_lock=self._execution_lock,
                supported_checks=(
                    "window_title",
                    "page_contains",
                    "page_element",
                    "active_window",
                    "page_value",
                    "pointer_position",
                ),
                check_validator=self._skill_adapters.validate_check,
            )
        return self._skill_service

    def _skills_changed(self):
        if self._publish_event:
            with suppress(Exception):
                self._publish_event({"type": "skills.changed"})

    async def _record_skill_result(self, result):
        if result.status in TERMINAL_STATUSES:
            await asyncio.to_thread(
                self._skills.record_outcome,
                result.skill_name,
                result.run_id,
                result.status,
            )
        self._skills_changed()

    def _watch_waiting_skill(self, service, run_id):
        if run_id in self._skill_monitors:
            return

        async def watch():
            try:
                result = await service.wait_for_terminal(run_id)
                await self._record_skill_result(result)
            except Exception:
                logger.exception("Could not account for completed skill run")
            finally:
                self._skill_monitors.pop(run_id, None)

        self._skill_monitors[run_id] = asyncio.create_task(watch())

    async def _skill_response(self, service, result):
        await self._record_skill_result(result)
        if result.status == "waiting":
            self._watch_waiting_skill(service, result.run_id)
            return _skill_json(
                {
                    "status": "waiting",
                    "run_id": result.run_id,
                    "question": result.question,
                    "question_id": result.question_id,
                    "input_name": result.input_name,
                }
            )
        if result.status not in TERMINAL_STATUSES:
            return json.dumps({"status": result.status, "run_id": result.run_id})
        if result.status != "done":
            raise ToolError(
                f"Skill {result.status} at step {result.failed_step}: {result.message}"[
                    :MODEL_OUTPUT_LIMIT
                ]
            )
        return f"{result.skill_name} completed: {result.completed_steps}/{result.total_steps} steps verified. Run {result.run_id}."

    @function_tool(flags=ToolFlag.CANCELLABLE, on_duplicate="reject")
    async def run_skill(
        self, context: RunContext, name: str, inputs_json: str = "{}"
    ) -> str:
        """Load a Hermes procedure and follow its returned instructions with actual tools; procedure_ready is not completion. Names may use procedure:category/name. Optional compiled workflows use workflow:slug; those execute verified steps and may return waiting for answer_skill."""
        run_id = None
        try:
            from tool_dispatch import repair_json_string

            inputs = json.loads(repair_json_string(inputs_json))
            if not isinstance(inputs, dict):
                raise ValueError("Skill inputs must be a JSON object.")
            procedure = (
                None
                if name.startswith("workflow:")
                else await asyncio.to_thread(self._skills_catalog.get_skill, name)
            )
            workflow_name = name.removeprefix("workflow:")
            skill = (
                None
                if name.startswith("procedure:")
                else await asyncio.to_thread(self._skills.get, workflow_name)
            )
            if procedure is not None:
                if skill is not None and not name.startswith("procedure:"):
                    raise ValueError(
                        "Ambiguous skill. Choose procedure:category/name or workflow:slug from list_skills."
                    )
                request = self._request_scope.current_request
                if request is None:
                    raise ValueError(
                        "A finalized current human request is required to load a procedure for execution."
                    )
                explicit = bool(
                    re.search(r"\b(?:run|use|follow|execute|try)\b", request.text, re.I)
                ) and normalized_name(procedure.name) in normalized_name(request.text)
                available = {tool.info.name for tool in self.tools}
                decision = await asyncio.to_thread(
                    decide_task,
                    request,
                    self._skills_catalog,
                    self._skills,
                    available_tools=available,
                    selected=procedure.canonical_id,
                    explicit=explicit,
                    inputs=inputs,
                )
                if decision.mode != "procedure":
                    raise ValueError(decision.reason)
                if not decision.validate_current(
                    self._request_scope.current_request, self._skills_catalog
                ):
                    raise ValueError(
                        "The selected procedure or current request changed; reselect against fresh evidence."
                    )
                call = getattr(context, "function_call", None)
                call_id = getattr(call, "call_id", None)
                previous_id = self._procedure_run_for(request.id)
                previous = (
                    self._skills_catalog.state_store.run(previous_id)
                    if previous_id
                    else None
                )
                previous_decision = getattr(self, "_procedure_decisions", {}).get(
                    request.id
                )
                unchanged = (
                    previous is not None
                    and previous_decision is not None
                    and previous_decision == decision
                )
                if unchanged:
                    run_id = previous_id
                else:
                    if previous and previous["status"] not in TERMINAL_STATUSES:
                        self._settle_replaced_procedure(previous)
                    run_id = (
                        f"{self._reporter.run_id}:{call_id}"
                        if call_id
                        else uuid.uuid4().hex
                    )
                loaded = await asyncio.to_thread(
                    self._skills_catalog.state_store.load_run,
                    run_id,
                    procedure,
                    decision,
                )
                if not hasattr(self, "_procedure_runs"):
                    self._procedure_runs = {}
                    self._procedure_decisions = {}
                self._procedure_runs[request.id] = run_id
                self._procedure_decisions[request.id] = decision
                self._last_skill_run_id = run_id
                self._active_task_decision = decision
                instructions = loaded["skill_markdown"]
                self._skills_changed()
                return json.dumps(
                    {
                        "status": "procedure_ready"
                        if loaded["status"] == "loaded"
                        else loaded["status"],
                        "mode": "procedure",
                        "name": procedure.name,
                        "run_id": run_id,
                        "request_id": request.id,
                        "canonical_id": decision.selected_id,
                        "document_digest": decision.document_digest,
                        "input_names": [item.name for item in decision.inputs],
                        "instructions": instructions,
                        "next": "Execute the procedure with available tools under the current human request, observe results, and continue until the requested goal is verified. This call only loaded instructions; no action has run.",
                    },
                    ensure_ascii=False,
                )
            if skill is None:
                skill = (
                    None
                    if name.startswith("procedure:")
                    else await asyncio.to_thread(
                        self._skills.find_by_phrase, workflow_name
                    )
                )
            if skill is None:
                raise ValueError(
                    "Skill not found. Use list_skills to inspect saved procedures."
                )
            entries = await asyncio.to_thread(self._skills.list)
            slug = next(
                entry.slug
                for entry in entries
                if entry.skill and entry.skill.name == skill.name
            )
            service = await self._get_skill_runtime()
            decision = None
            request = self._request_scope.current_request
            if request is not None:
                explicit = bool(
                    re.search(r"\b(?:run|use|follow|execute|try)\b", request.text, re.I)
                ) and normalized_name(skill.name) in normalized_name(request.text)
                decision = await asyncio.to_thread(
                    decide_task,
                    request,
                    self._skills_catalog,
                    self._skills,
                    available_tools=self._skill_registry.names(),
                    selected="workflow:" + slug,
                    explicit=explicit,
                    inputs=inputs,
                )
                if decision.mode != "workflow":
                    raise ValueError(decision.reason)
                if not decision.validate_current(
                    self._request_scope.current_request,
                    self._skills_catalog,
                    self._skills,
                ):
                    raise ValueError(
                        "The workflow identity or current request changed."
                    )
                inputs = dict(decision.input_values)
                service.validate(
                    skill, self._skill_registry, inputs, require_enabled=False
                )
                if explicit and skill.state != "enabled":
                    # A current, explicit validated draft run uses the same
                    # version and engine without permanently enabling reuse.
                    skill = replace(skill, state="enabled")
            call = getattr(context, "function_call", None)
            call_id = getattr(call, "call_id", None)
            run_id = (
                f"{self._reporter.run_id}:{call_id}" if call_id else uuid.uuid4().hex
            )
            self._last_skill_run_id = run_id
            if context is not None and hasattr(context, "update"):
                await context.update(f"Starting {skill.name}.")
            with service.run_store.bind_task(decision):
                result = await service.run(
                    skill,
                    self._scoped_tools.registry_for_current_request(
                        self._skill_registry
                    ),
                    self._reporter.for_tool(context),
                    inputs=inputs,
                    run_id=run_id,
                    skill_slug=slug,
                )
            return await self._skill_response(service, result)
        except asyncio.CancelledError:
            if self._skill_service and run_id:
                record = await asyncio.to_thread(self._skill_service.status, run_id)
                if record and record["status"] in {
                    "done",
                    "error",
                    "cancelled",
                    "uncertain",
                }:
                    await asyncio.to_thread(
                        self._skills.record_outcome,
                        record["skill_name"],
                        record["run_id"],
                        record["status"],
                    )
                    self._skills_changed()
            raise
        except ToolError:
            raise
        except (ValueError, OSError) as exc:
            raise ToolError(str(exc)[:MODEL_OUTPUT_LIMIT]) from exc

    @function_tool(flags=ToolFlag.CANCELLABLE, on_duplicate="reject")
    async def answer_skill(
        self, context: RunContext, answer: str, run_id: str, question_id: str
    ) -> str:
        """Resume a waiting skill with the user's answer and exact run/question IDs from run_skill or skill_status. Never invent an answer. A new waiting result needs a new user answer."""
        if self._skill_service is None:
            raise ToolError("No skill is waiting in this session.")
        try:
            result = await self._skill_service.answer(
                run_id,
                answer,
                question_id=question_id,
            )
            return await self._skill_response(self._skill_service, result)
        except (ValueError, OSError) as exc:
            raise ToolError(str(exc)[:MODEL_OUTPUT_LIMIT]) from exc

    @function_tool
    async def stop_skill(self, context: RunContext, run_id: str = "") -> str:
        """Stop the current skill or a specified run before further steps execute."""
        target = run_id or self._last_skill_run_id
        procedure = (
            await asyncio.to_thread(self._skills_catalog.state_store.run, target)
            if target
            else None
        )
        if procedure:
            await asyncio.to_thread(
                self._skills_catalog.state_store.finish_run,
                target,
                procedure["request_id"],
                "cancelled",
            )
            self._skills_changed()
            return "Procedure stopped; its goal was not verified."
        if not target or self._skill_service is None:
            return "No skill is running."
        if not self._skill_service.stop(target):
            return "That skill run is already finished or was not found."
        return "Stop requested. An action already in progress may still finish; check skill_status."

    @function_tool
    async def skill_status(self, context: RunContext, run_id: str = "") -> str:
        """Read the recorded outcome of the latest or specified skill run."""
        target = run_id or self._last_skill_run_id
        if not target:
            return "No skill run in this session."
        procedure = await asyncio.to_thread(
            self._skills_catalog.state_store.run, target
        )
        if procedure:
            return _skill_json(
                {
                    key: procedure.get(key)
                    for key in (
                        "run_id",
                        "mode",
                        "status",
                        "request_id",
                        "current_goal",
                        "selection_reason",
                        "completed_steps",
                        "total_steps",
                        "verified",
                        "message",
                    )
                }
            )
        service = await self._get_skill_runtime()
        record = await asyncio.to_thread(service.status, target)
        if not record:
            return "Run not found."
        if record["status"] in TERMINAL_STATUSES:
            # Idempotent, including a Stop/expiry racing the waiting monitor.
            await asyncio.to_thread(
                self._skills.record_outcome,
                record["skill_name"],
                target,
                record["status"],
            )
        return _skill_json(
            {
                k: record.get(k)
                for k in (
                    "run_id",
                    "status",
                    "completed_steps",
                    "total_steps",
                    "failed_step",
                    "message",
                    "question",
                    "question_id",
                    "input_name",
                )
            }
        )

    @function_tool
    async def list_skills(self, context: RunContext) -> str:
        """List Hermes procedures (follow with tools) and optional compiled workflows (draft/enabled)."""
        rows = await asyncio.to_thread(self._skills.catalog)
        procedures = await self.skills_list(context)
        workflows = "\n".join(
            f"workflow:{r['slug']} [compiled, {r['state']}]: {r['description']}"
            for r in rows
        )
        return procedures + (
            "\nOptional compiled workflows:\n" + workflows if workflows else ""
        )

    @function_tool
    async def view_skill(self, context: RunContext, name: str) -> str:
        """Read a Hermes procedure by name (alias of skill_view) or a workflow:slug specification."""
        if not name.startswith("workflow:"):
            return await self.skill_view(context, name)
        return await self._view_compiled_skill(name.removeprefix("workflow:"))

    async def _view_compiled_skill(self, name: str) -> str:
        skill = await asyncio.to_thread(self._skills.get, name)
        if skill is None:
            skill = await asyncio.to_thread(self._skills.find_by_phrase, name)
        if skill is None:
            return f"Skill '{name}' not found. Use list_skills to inspect available procedures."
        from skill_format import render_skill

        return "Mode: compiled workflow\n" + render_skill(skill)

    async def save_skill(self, context: RunContext, markdown: str) -> str:
        """Save a user-provided procedure as a draft Markdown skill; saving never enables execution."""
        try:
            skill = replace(parse_skill(markdown), state="draft", source="dictated")
            saved = await asyncio.to_thread(self._skills.save, skill, source="dictated")
        except (ValueError, OSError) as exc:
            raise ToolError(str(exc)[:MODEL_OUTPUT_LIMIT]) from exc
        self._skills_changed()
        return f"Saved {saved.name} version {saved.version} as a draft. Review the steps before enabling."

    async def enable_skill(self, context: RunContext, name: str) -> str:
        """Enable a reviewed skill when the user explicitly asks. Validates permissions and checks without executing."""
        try:
            procedure = (
                None
                if name.startswith("workflow:")
                else await asyncio.to_thread(self._skills_catalog.get_skill, name)
            )
            if procedure is not None:
                if (
                    not name.startswith("procedure:")
                    and await asyncio.to_thread(self._skills.get, name) is not None
                ):
                    raise ValueError(
                        "Ambiguous skill name; choose the procedure or workflow from list_skills."
                    )
                await asyncio.to_thread(
                    self._skills_catalog.enable_skill,
                    procedure.canonical_id,
                    available_tools={tool.info.name for tool in self.tools},
                )
                self._skills_changed()
                return f"Enabled {procedure.name} for automatic selection when it fits the current task."
            if name.startswith("procedure:"):
                raise ValueError("Procedure not found.")
            skill = await asyncio.to_thread(
                self._skills.get, name.removeprefix("workflow:")
            )
            if skill is None:
                raise ValueError("Skill not found.")
            service = await self._get_skill_runtime()
            service.validate(skill, self._skill_registry, require_enabled=False)
            saved = await asyncio.to_thread(
                self._skills.save, replace(skill, state="enabled"), source="edited"
            )
        except (ValueError, OSError) as exc:
            raise ToolError(str(exc)[:MODEL_OUTPUT_LIMIT]) from exc
        self._skills_changed()
        return f"Enabled {saved.name} version {saved.version}."

    async def undo_skill_change(self, context: RunContext, name: str) -> str:
        """Restore a previous skill version as a new draft when requested."""
        result = await asyncio.to_thread(self._skills.undo, name)
        if result:
            self._skills_changed()
        return (
            f"Restored {result.name} version {result.version}."
            if result
            else "No earlier version found."
        )

    async def delete_skill(self, context: RunContext, name: str) -> str:
        """Archive a skill when requested; its versions remain recoverable."""
        procedure = (
            None
            if name.startswith("workflow:")
            else await asyncio.to_thread(self._skills_catalog.get_skill, name)
        )
        if procedure is not None:
            changed = await asyncio.to_thread(
                self._skills_catalog.archive_skill, procedure.canonical_id
            )
        elif name.startswith("procedure:"):
            changed = False
        else:
            changed = await asyncio.to_thread(
                self._skills.delete, name.removeprefix("workflow:")
            )
        self._skills_changed()
        return "Skill archived." if changed else "Skill not found."

    async def learn_skill(
        self, context: RunContext, name: str, run_id: str = ""
    ) -> str:
        """Save a reviewed procedure from a verified successful skill run as a new draft."""
        from skill_learning import learn_from_run

        try:
            service = await self._get_skill_runtime()
            selected_run = run_id or self._last_skill_run_id
            adaptive = await asyncio.to_thread(
                self._skills_catalog.state_store.run, selected_run
            )
            result = await asyncio.to_thread(
                learn_from_run,
                selected_run,
                name,
                self._skills,
                self._skills_catalog.state_store.run_store
                if adaptive
                else service.run_store,
                catalog=self._skills_catalog,
            )
        except (ValueError, OSError) as exc:
            raise ToolError(str(exc)) from exc
        self._skills_changed()
        return f"Learned {result.name} as a draft from verified work. Review before enabling."

    async def curate_skills(self, context: RunContext, action: str = "suggest") -> str:
        """Run or inspect library curation (Hermes curator architecture: suggest, transitions, clusters)."""
        from curator import SkillCurator

        curator = SkillCurator(self._skills)
        act = action.lower().strip()
        if act == "suggest":
            suggestions = await asyncio.to_thread(curator.suggest)
            if not suggestions:
                return "All skills are active, healthy, and up to date, boss."
            lines = [
                f"- {s['name']} ({s['slug']}): {s['action']} — {s['reason']}"
                for s in suggestions
            ]
            return "Curator recommendations:\n" + "\n".join(lines)
        elif act in ("transitions", "auto", "maintain"):
            result = await asyncio.to_thread(curator.apply_automatic_transitions)
            self._skills_changed()
            return (
                f"Curator maintenance complete: {result['archived']} archived, "
                f"{result['stale']} stale."
            )
        elif act == "clusters":
            candidates = await asyncio.to_thread(curator.find_consolidation_candidates)
            if not candidates:
                return "No skill clusters found for consolidation, boss."
            lines = [
                f"- Prefix '{c['cluster_prefix']}': {c['skill_count']} skills ({', '.join(c['slugs'])}) — {c['recommendation']}"
                for c in candidates
            ]
            return "Consolidation candidates:\n" + "\n".join(lines)
        else:
            return f"Unknown curator action '{action}'. Choose suggest, transitions, or clusters."

    @function_tool
    async def train_tool(self, context: RunContext, tool_name: str) -> str:
        """Practice and run calibration drills on a tool to adapt parameters, resolve failure signatures, and elevate mastery level."""
        store = getattr(self, "_tool_mastery", None)
        if store is None:
            from tool_mastery import ToolMasteryStore

            store = ToolMasteryStore()
            self._tool_mastery = store

        from tool_mastery import ToolTrainer

        trainer = getattr(self, "_tool_trainer", None)
        if trainer is None:
            trainer = ToolTrainer(store)
            self._tool_trainer = trainer

        result = await asyncio.to_thread(trainer.train_tool, tool_name)
        if result.get("success"):
            return (
                f"Training complete, sir. {result.get('message')} "
                f"Calibrated parameters: {json.dumps(result.get('calibrated_params', {}))}."
            )
        else:
            return f"Training drill encountered an issue, sir: {result.get('message', result.get('error'))}"

    @function_tool
    async def tool_mastery_status(
        self, context: RunContext, tool_name: str = ""
    ) -> str:
        """Inspect tool mastery levels (Novice, Practiced, Mastered), success rates, and calibrated parameters."""
        store = getattr(self, "_tool_mastery", None)
        if store is None:
            from tool_mastery import ToolMasteryStore

            store = ToolMasteryStore()
            self._tool_mastery = store

        if tool_name.strip():
            norm = tool_name.strip().lower()
            profile = store.get_profile(norm)
            rate = (
                f"{(profile.success_count / profile.use_count * 100):.1f}%"
                if profile.use_count > 0
                else "N/A"
            )
            return (
                f"Tool '{norm}': Level {profile.level}. "
                f"Success rate: {rate} ({profile.success_count}/{profile.use_count}, "
                f"{profile.consecutive_successes} consecutive). "
                f"Calibrated parameters: {json.dumps(profile.calibrated_params)}. "
                f"Failure signatures: {json.dumps(profile.failure_signatures)}."
            )
        else:
            cat = store.catalog()
            if not cat:
                return "No tool mastery records logged yet, boss."
            lines = [
                f"- {item['tool']}: {item['level']} ({item['success_rate']} across {item['use_count']} uses)"
                for item in cat
            ]
            return "Tool mastery ledger:\n" + "\n".join(lines)
