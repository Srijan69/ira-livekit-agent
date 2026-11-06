"""LiveKit tool facade and saved-skill propagation for request admission.

Install on Assistant._tools after Agent.__init__, attach in on_enter, and use
text_input_cb for room text input. Conversation messages deliberately do not
admit requests: screenshot evidence is also carried in user-role messages.
"""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import json
import logging
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps

from livekit.agents.llm import FunctionTool, RawFunctionTool
from livekit.agents.llm.utils import build_legacy_openai_schema

from request_scope import ActionProposal, RequestIdentity, RequestScope, ScopeViolation


@dataclass
class _Binding:
    scope: RequestScope
    request: RequestIdentity
    speech: object = None


logger = logging.getLogger(__name__)

_binding = contextvars.ContextVar("ira_request_binding", default=None)
_action = contextvars.ContextVar("ira_action_permit", default=None)
_outer_actions = contextvars.ContextVar("ira_outer_action_permits", default=())


def check_current_action():
    """No-op for internal primitives; guarded exported effects must remain live."""
    for scope, permit, speech in _outer_actions.get():
        if getattr(speech, "interrupted", False):
            raise ScopeViolation(
                "An originating speech was interrupted before this effect"
            )
        scope.check(permit)
    binding = _binding.get()
    if binding is not None and getattr(binding.speech, "interrupted", False):
        raise ScopeViolation(
            "The originating speech was interrupted before this effect"
        )
    active = _action.get()
    if active is not None:
        scope, permit = active
        scope.check(permit)
    elif _binding.get() is not None:
        raise ScopeViolation("No action permit is active")


_OBSERVATIONS = frozenset(
    {
        "capture_screen",
        "capture_screen_detail",
        "desktop_detail",
        "inspect_screen",
        "read_page",
        "list_page_elements",
        "list_desktop_windows",
        "describe_desktop",
        "list_tabs",
        "scrape_market_assets",
    }
)
_LOCAL_READS = frozenset(
    {
        "current_time",
        "system_status",
        "list_tasks",
        "list_skills",
        "skills_list",
        "skill_status",
        "recall",
        "list_preferences",
        "load_capabilities",
        "inspect_skill",
        "view_skill",
        "skill_view",
        "skill_manage",
        "describe_page",
        "tool_mastery_status",
        "list_workflows",
    }
)
_STOP_TOOLS = frozenset({"stop_actions", "stop_skill"})


class RequestScopeTools:
    def __init__(
        self,
        scope: RequestScope,
        *,
        observation=None,
        on_stop=None,
        final_transcript_timeout=2.0,
    ):
        if final_transcript_timeout <= 0:
            raise ValueError("Final transcription timeout must be positive")
        self.final_transcript_timeout = final_transcript_timeout
        self.scope = scope
        self.observation = observation
        self.on_stop = on_stop
        self._session = None
        self._callbacks = []
        self._speeches = OrderedDict()
        self._handles = {}
        self._speech_sources = OrderedDict()
        self._waiting = {}
        self._clarification = None
        self._continuations = OrderedDict()
        self._background = set()
        self._running = {}
        self._activity_revision = 0
        self._reviewed = set()
        self._final_voice_requests = OrderedDict()
        self._retired_sources = OrderedDict()
        self._invalid_speeches = set()
        self._speech_epochs = {}
        self._completed_calls = OrderedDict()
        self._binding_changed = asyncio.Event()
        self._closed = False

    def bind_speech(self, speech_handle, request):
        if self._closed:
            return
        self._activity_revision += 1
        self._speeches[speech_handle.id] = request
        self._speeches.move_to_end(speech_handle.id)
        if speech_handle.id not in self._handles:
            self._handles[speech_handle.id] = speech_handle
            speech_handle.add_done_callback(self._speech_done)
        while len(self._speeches) > 256:
            old, _ = self._speeches.popitem(last=False)
            self._speech_sources.pop(old, None)
            self._reviewed.discard(old)
            self._invalid_speeches.discard(old)
            self._speech_epochs.pop(old, None)
            self._handles.pop(old).remove_done_callback(self._speech_done)

    def _speech_done(self, handle):
        if not self._closed:
            task = asyncio.create_task(self.review_completed_speech(handle))
            self._background.add(task)
            task.add_done_callback(self._background_done)

    def _background_done(self, task):
        self._background.discard(task)
        if not task.cancelled():
            task.exception()

    def _can_review(self, handle, request):
        if (
            self._closed
            or request is None
            or self._speeches.get(handle.id) != request
            or not handle.done()
            or handle.interrupted
            or handle.exception() is not None
            or any(item.type == "function_call" for item in handle.chat_items)
            or any(
                binding.request.id == request.id for binding in self._running.values()
            )
            or any(
                waiting[0].request.id == request.id
                for waiting in self._waiting.values()
            )
            or (self._clarification and self._clarification[0].id == request.id)
        ):
            return False
        return not any(
            self._speeches.get(speech_id) == request and not other.done()
            for speech_id, other in self._handles.items()
        )

    async def review_completed_speech(self, handle):
        """Close only a supported final report after all bound work has settled."""
        request = self._speeches.get(handle.id)
        if handle.id in self._reviewed or not self._can_review(handle, request):
            return
        report = "\n".join(
            item.text_content
            for item in handle.chat_items
            if item.type == "message" and item.role == "assistant" and item.text_content
        )
        if not report or len(report) > 16000:
            return
        self._reviewed.add(handle.id)
        revision = self._activity_revision
        try:
            await self.scope.assess_completion(request.id, report, self._metadata())
            # A new tool can start (and even finish) while the judge awaits.
            # Both identity and work revision must still match the reviewed turn.
            if revision != self._activity_revision or not self._can_review(
                handle, request
            ):
                return
            self.scope.finish(request.id)
        except (ScopeViolation, ValueError, RuntimeError):
            # Policy outages and stale evidence leave authority unexpanded. No
            # effects are issued by this fallback, even for unresolved closure.
            return
        self._continuations.pop(request.id, None)

    @contextmanager
    def _track(self, binding):
        ticket = object()
        self._running[ticket] = binding
        self._activity_revision += 1
        try:
            yield
        finally:
            self._running.pop(ticket, None)

    def _signal_binding_changed(self):
        event, self._binding_changed = self._binding_changed, asyncio.Event()
        event.set()

    def _invalidate_voice(self, *, except_source=None):
        for speech_id, source in self._speech_sources.items():
            if source != except_source:
                self._invalid_speeches.add(speech_id)
                self._retired_sources[source] = None
        while len(self._retired_sources) > 512:
            self._retired_sources.popitem(last=False)
        self._continuations.clear()
        self._completed_calls.clear()
        self._signal_binding_changed()

    async def _request_for_context(
        self, context, *, tool_name=None, tool_arguments=None
    ):
        """Wait only for the final input identified by this registered generation."""
        speech = getattr(context, "speech_handle", None)
        speech_id = getattr(speech, "id", None)
        source = self._speech_sources.get(speech_id)
        deadline = asyncio.get_running_loop().time() + self.final_transcript_timeout
        while True:
            event = self._binding_changed
            if self._closed:
                raise ScopeViolation("Session closed", status="session_closed")
            if getattr(speech, "interrupted", False):
                raise ScopeViolation(
                    "The originating speech was interrupted", status="interrupted"
                )
            if speech_id in self._invalid_speeches or (
                speech_id in self._speech_epochs
                and self._speech_epochs[speech_id] != self.scope._epoch
            ):
                raise ScopeViolation(
                    "The originating generation was invalidated", status="stale_request"
                )
            request = self._speeches.get(speech_id)
            if request is not None:
                self.scope.require(request.id)
                return request
            if not source:
                raise ScopeViolation(
                    "Tool has no identified finalized human input",
                    status="unbound_input",
                )
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise ScopeViolation(
                    "Matching final transcription did not arrive in time",
                    status="awaiting_final_input",
                )
            try:
                await asyncio.wait_for(event.wait(), remaining)
            except TimeoutError:
                raise ScopeViolation(
                    "Matching final transcription did not arrive in time",
                    status="awaiting_final_input",
                ) from None

    def _metadata(self):
        if self.observation is None:
            return {}
        result = self.observation()
        return dict(result) if result is not None else {}

    async def _execute(
        self, binding, name, arguments, operation, *, kind="effect", observation=None
    ):
        # Register before the policy await: unissued permits are still work.
        with self._track(binding):
            return await self._execute_with_permit(
                binding, name, arguments, operation, kind=kind, observation=observation
            )

    async def _execute_with_permit(
        self, binding, name, arguments, operation, *, kind, observation
    ):
        permit = await self.scope.admit(
            binding.request.id,
            ActionProposal(
                name,
                arguments,
                kind,
                self._metadata() if observation is None else observation,
            ),
        )
        try:
            self.scope.consume(permit)
        except BaseException:
            self.scope.record(
                permit, "not_started", "Admission invalidated before execution"
            )
            raise
        outer_token = None
        prior_action = _action.get()
        if prior_action is not None and prior_action[0] is not self.scope:
            # Rewrapping a tool under another controller must keep both guards
            # live at the native boundary. Same-controller skill steps replace
            # their orchestrator permit as before.
            prior_binding = _binding.get()
            outer_token = _outer_actions.set(
                (
                    *_outer_actions.get(),
                    (*prior_action, getattr(prior_binding, "speech", None)),
                )
            )
        bound_token = _binding.set(binding)
        action_token = _action.set((self.scope, permit))
        try:
            check_current_action()
            result = await operation()
        except BaseException as exc:
            outcome = (
                "not_started"
                if getattr(exc, "effect_started", None) is False
                else "uncertain"
            )
            self.scope.record(permit, outcome, type(exc).__name__)
            raise
        else:
            outcome = "done" if getattr(result, "ok", None) is True else "returned"
            if getattr(result, "ok", None) is False:
                outcome = "error"
            self.scope.record(permit, outcome, str(result))
            return result
        finally:
            _action.reset(action_token)
            _binding.reset(bound_token)
            if outer_token is not None:
                _outer_actions.reset(outer_token)

    def wrap_tools(self, tools):
        wrapped = []
        for tool in tools:
            if not isinstance(tool, (FunctionTool, RawFunctionTool)):
                raise TypeError(
                    "All exported effects must be supported FunctionTool objects"
                )
            if getattr(tool, "_ira_scope_facade", None) is self:
                wrapped.append(tool)
                continue
            wrapped.append(self._wrap_tool(tool))
        return wrapped

    def _wrap_tool(self, tool):
        from tool_dispatch import _ArgumentRepairTool

        if isinstance(tool, _ArgumentRepairTool):
            # The adapter has a raw provider signature and a different
            # constructor. Add this guard to its typed callable, retaining any
            # existing scope facade, then rebuild argument repair below.
            return self._wrap_tool(tool._original)
        signature = inspect.signature(tool)
        name = tool.info.name
        inputs_json_is_string = False
        if "inputs_json" in signature.parameters and isinstance(tool, FunctionTool):
            schema = build_legacy_openai_schema(tool, internally_tagged=True)
            inputs_json_is_string = (
                schema["parameters"]["properties"]["inputs_json"].get("type")
                == "string"
            )

        @wraps(tool)
        async def call(*args, **kwargs):
            from tool_dispatch import repair_tool_arguments

            expected = set(signature.parameters.keys())
            if kwargs:
                original_inputs = kwargs.get("inputs_json", kwargs.get("inputs"))
                kwargs = repair_tool_arguments(kwargs, expected_params=expected)
                if (
                    "inputs_json" in expected
                    and isinstance(original_inputs, dict)
                    and not inputs_json_is_string
                ):
                    kwargs["inputs_json"] = original_inputs
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            arguments = dict(bound.arguments)
            context = arguments.pop("context", arguments.pop("ctx", None))
            arguments.pop("self", None)
            if name in _STOP_TOOLS:
                await self.stop_current_request()
                return await tool(*args, **kwargs)
            try:
                request = await self._request_for_context(context)
            except ScopeViolation:
                if name in _LOCAL_READS:
                    # Reads remain usable without authority, but their results
                    # cannot establish an origin for subsequent effects.
                    return await tool(*args, **kwargs)
                raise
            binding = _Binding(
                self.scope, request, getattr(context, "speech_handle", None)
            )
            with self._remember_call_origin(binding, context, name):
                if name in _LOCAL_READS:
                    return await tool(*args, **kwargs)
                if name in {"finish_request", "clarify_request"}:
                    token = _binding.set(binding)
                    try:
                        return await tool(*args, **kwargs)
                    finally:
                        _binding.reset(token)
                if name != "answer_skill":
                    await self._renew_clarification(binding, context)
                    request = binding.request
                kind = "observe" if name in _OBSERVATIONS else "effect"
                if name in {"run_skill", "answer_skill"}:
                    kind = "orchestrate"
                observation = self._metadata()
                waiting = (
                    self._waiting.get(arguments.get("run_id"))
                    if name == "answer_skill"
                    else None
                )
                if name == "answer_skill":
                    if not waiting or waiting[1] != arguments.get("question_id"):
                        raise ScopeViolation(
                            "That skill question is no longer awaiting an answer"
                        )
                    observation["pending_skill"] = {
                        "original_request": waiting[0].request.text,
                        "human_answers": waiting[0].request.answers,
                        "question": waiting[2],
                    }

                async def invoke():
                    if waiting:
                        # Judge has now approved the actual new human answer. Renew
                        # only this waiting worker; old speech handles remain stale.
                        continued = self.scope.continue_with_answer(
                            waiting[0].request, request.id
                        )
                        waiting[0].request = continued
                        waiting[0].speech = context.speech_handle
                        binding.request = continued
                        self.bind_speech(context.speech_handle, continued)
                        # answer_skill wakes the engine but has no host effects itself.
                    result = await tool(*args, **kwargs)
                    if waiting:
                        # Invalid answers leave the engine waiting. Consume this
                        # question only after its answer handler actually succeeds.
                        self._waiting.pop(arguments["run_id"], None)
                    if name in {"run_skill", "answer_skill"}:
                        self._remember_waiting(
                            result, waiting[0] if waiting else binding
                        )
                    return result

                result = await self._execute(
                    binding, name, arguments, invoke, kind=kind, observation=observation
                )
                return result

        call.__signature__ = signature
        result = type(tool)(call, tool.info)
        result._ira_scope_facade = self
        from tool_dispatch import repair_function_tool

        return repair_function_tool(result)

    @contextmanager
    def _remember_call_origin(self, binding, context, name):
        """Publish only the validated origin, including reads and failed calls."""
        with self._track(binding):
            try:
                yield
            finally:
                call_id = getattr(
                    getattr(context, "function_call", None), "call_id", None
                )
                if call_id:
                    self._completed_calls[call_id] = (
                        binding.request,
                        name,
                        binding.speech,
                    )
                    while len(self._completed_calls) > 256:
                        self._completed_calls.popitem(last=False)

    def _remember_waiting(self, result, binding):
        try:
            data = json.loads(result) if isinstance(result, str) else result
        except (TypeError, ValueError):
            return
        if isinstance(data, dict) and data.get("status") == "waiting":
            self._waiting[data["run_id"]] = (
                binding,
                data["question_id"],
                data.get("question", ""),
            )
            if len(self._waiting) > 16:
                self._waiting.pop(next(iter(self._waiting)))

    def wrap_registry(self, registry):
        from skill_execution import ToolRegistry

        handlers = {}
        for name, handler in registry._tools.items():

            def make_handler(name, handler):
                @wraps(handler)
                async def call(**arguments):
                    binding = _binding.get()
                    if binding is None or binding.scope is not self.scope:
                        raise ScopeViolation(
                            "Saved skill has no originating human request"
                        )

                    async def invoke():
                        check_current_action()
                        if inspect.iscoroutinefunction(handler):
                            return await handler(**arguments)
                        return await asyncio.to_thread(handler, **arguments)

                    observation = self._metadata()
                    if name.startswith("desktop_"):
                        observation["action_coordinate_space"] = "native_desktop"

                    return await self._execute(
                        binding,
                        name,
                        arguments,
                        invoke,
                        kind="observe" if name in _OBSERVATIONS else "effect",
                        observation=observation,
                    )

                return call

            handlers[name] = make_handler(name, handler)
        return ToolRegistry(handlers, validators=registry._validators)

    def registry_for_current_request(self, registry):
        """Keep internal engine callers usable; exported run_skill is always bound."""
        binding = _binding.get()
        if binding is None:
            return registry
        if binding.scope is not self.scope:
            raise ScopeViolation(
                "Saved skill belongs to a different request controller"
            )
        self.scope.require(binding.request.id)
        return self.wrap_registry(registry)

    def clarify_request(self, question):
        binding = _binding.get()
        if binding is None or binding.scope is not self.scope:
            raise ScopeViolation("No human request is bound to this question")
        self.scope.require(binding.request.id)
        if not isinstance(question, str) or not 1 <= len(question.strip()) <= 1000:
            raise ScopeViolation("Provide one short clarification question")
        self._clarification = (binding.request, question.strip())
        self.scope.suspend("Awaiting the user's clarification before further actions")
        return question.strip()

    async def _renew_clarification(self, binding, context):
        with self._track(binding):
            await self._renew_clarification_tracked(binding, context)

    async def _renew_clarification_tracked(self, binding, context):
        pending = self._clarification
        if pending is None:
            return
        original, question = pending
        if binding.request.id == original.id:
            raise ScopeViolation("Wait for the user's answer before further actions")
        try:
            permit = await self.scope.admit(
                binding.request.id,
                ActionProposal(
                    "answer_request",
                    {"answer": binding.request.text},
                    "orchestrate",
                    {
                        "pending_request": {
                            "original_request": original.text,
                            "human_answers": original.answers,
                            "question": question,
                        }
                    },
                ),
            )
        except ScopeViolation as exc:
            if exc.decision is None:
                raise
            # A genuinely new request retains only its own authority. The
            # actual effect is still judged below, even after a denied answer.
            self._clarification = None
            return
        self.scope.consume(permit)
        self.scope.record(permit, "done", "Human clarification admitted")
        binding.request = self.scope.continue_with_answer(original, binding.request.id)
        self.bind_speech(context.speech_handle, binding.request)
        self._clarification = None

    async def stop_current_request(self):
        self._stop_scope()
        await self._notify_stop()
        return "Stopped. Further actions require a new human request."

    def _stop_scope(self):
        self.scope.stop()
        self._invalidate_voice()
        self._waiting.clear()
        self._clarification = None
        self._continuations.clear()

    async def _notify_stop(self):
        if self.on_stop:
            result = self.on_stop()
            if inspect.isawaitable(result):
                await result

    def finish_current_request(self):
        binding = _binding.get()
        if binding is None or binding.scope is not self.scope:
            raise ScopeViolation("No request is bound to this completion")
        self.scope.finish(binding.request.id)
        self._invalidate_voice()
        self._waiting.clear()
        self._clarification = None
        return "Request closed. Report only outcomes supported by actual observations."

    def attach_session(self, session):
        if self._session is session:
            return
        if self._session is not None:
            self.scope.revoke("Session changed; await new human input")
            self._waiting.clear()
            self._clarification = None
            self._continuations.clear()
            self._invalidate_voice()
            for event, callback in self._callbacks:
                self._session.off(event, callback)
        self._session = session
        self._callbacks = [
            ("user_input_transcribed", self._on_transcript),
            ("speech_created", self._on_speech),
            ("function_tools_executed", self._on_tools_executed),
            ("close", self._on_close),
        ]
        for event, callback in self._callbacks:
            session.on(event, callback)

    def _on_close(self, _event):
        self._closed = True
        self.scope.revoke("Session closed")
        self._invalidate_voice()

    async def aclose(self):
        """Call after owned host jobs settle; detach events and close owned policy IO."""
        self._closed = True
        self.scope.revoke("Request admission closed")
        if self._session is not None:
            for event, callback in self._callbacks:
                self._session.off(event, callback)
        self._session = None
        self._callbacks.clear()
        self._waiting.clear()
        self._clarification = None
        self._continuations.clear()
        self._invalidate_voice()
        for handle in self._handles.values():
            handle.remove_done_callback(self._speech_done)
        self._handles.clear()
        self._speeches.clear()
        self._speech_sources.clear()
        self._reviewed.clear()
        for task in self._background:
            task.cancel()
        if self._background:
            await asyncio.gather(*self._background, return_exceptions=True)
        close = getattr(self.scope.judge, "aclose", None)
        if close is not None:
            await close()

    def _on_tools_executed(self, event):
        """Arm continuation only from SDK-published results of our bound calls."""
        self._continuations.clear()
        origins = {}
        for call, output in zip(event.function_calls, event.function_call_outputs):
            completed = self._completed_calls.pop(call.call_id, None)
            if (
                completed is None
                or completed[1] != call.name
                or output.call_id != call.call_id
            ):
                continue
            request, _, speech = completed
            if getattr(speech, "interrupted", False):
                continue
            try:
                self.scope.require(request.id)
            except ScopeViolation:
                continue
            logger.info(
                "tool_result_published request_id=%s call_id=%s tool=%s",
                request.id,
                call.call_id,
                call.name,
            )
            if output.reply_required:
                origins[request.id] = request
        if len(origins) == 1 and not self._closed:
            self._continuations.update(origins)

    def _on_speech(self, event):
        if self._closed:
            return
        # In Google 1.8.8 input_id exists before generation_created, which
        # synchronously emits speech_created through AgentActivity.
        activity = getattr(self._session, "_activity", None)
        realtime = getattr(activity, "_rt_session", None)
        realtime = getattr(realtime, "_active", realtime)
        generation = getattr(realtime, "_current_generation", None)
        source = (
            getattr(generation, "input_id", None) if not event.user_initiated else None
        )
        binding = _binding.get()
        request = binding.request if binding and binding.scope is self.scope else None
        if request is None and not event.user_initiated:
            request = self._final_voice_requests.get(source)
            if request is None and len(self._continuations) == 1:
                request = next(iter(self._continuations.values()))
            self._continuations.clear()
        handle = event.speech_handle
        self.bind_speech(handle, request)
        self._speech_epochs[handle.id] = self.scope._epoch
        if source:
            self._speech_sources[handle.id] = source
            if source in self._retired_sources:
                self._invalid_speeches.add(handle.id)
        self._signal_binding_changed()

    def _on_transcript(self, event):
        if self._closed:
            return
        clean_text = event.transcript.strip()
        if clean_text.lower().rstrip(".!?") in {
            "stop",
            "stop ira",
            "ira stop",
            "cancel",
            "stop everything",
        }:
            self._stop_scope()
            task = asyncio.create_task(self._notify_stop())
            self._background.add(task)
            task.add_done_callback(self._background_done)
            return
        source = event.item_id
        if not source:
            # Empty interim events are also emitted on detected new speech.
            self.scope.suspend("Awaiting finalized human input with a source identity")
            self._invalidate_voice()
            return
        if source in self._retired_sources:
            return
        if any(
            speech_source == source
            and self._speech_epochs.get(speech_id) != self.scope._epoch
            for speech_id, speech_source in self._speech_sources.items()
        ):
            self._retired_sources[source] = None
            self._signal_binding_changed()
            return
        if not clean_text:
            return
        prior = self._final_voice_requests.get(source)
        if prior is not None:
            if clean_text != prior.text:
                self.scope.revoke("Finalized voice input changed")
                self._invalidate_voice()
            return
        self._invalidate_voice(except_source=source)
        if not event.is_final:
            self.scope.suspend("User is speaking; await the finalized instruction")
            self._signal_binding_changed()
            return
        request = self.scope.begin_request(clean_text, source_id=f"voice:{source}")
        self._final_voice_requests[source] = request
        while len(self._final_voice_requests) > 256:
            self._final_voice_requests.popitem(last=False)
        for speech_id, speech_source in tuple(self._speech_sources.items()):
            if speech_source == source and speech_id not in self._invalid_speeches:
                self._speeches[speech_id] = request
        self._signal_binding_changed()

    async def text_input_cb(self, session, event):
        """Trusted lk.chat boundary; admit before generate_reply creates tasks."""
        self.attach_session(session)
        if self._closed:
            raise ScopeViolation("Session closed", status="session_closed")
        self._invalidate_voice()
        stream_id = getattr(getattr(event, "info", None), "stream_id", None)
        request = self.scope.begin_request(
            event.text, source_id=f"text:{stream_id or uuid.uuid4().hex}"
        )
        token = _binding.set(_Binding(self.scope, request))
        try:
            async with session._claim_user_turn():
                await session.interrupt()
                if event.text.strip().lower().rstrip(".!?") in {
                    "stop",
                    "stop ira",
                    "ira stop",
                    "cancel",
                    "stop everything",
                }:
                    await self.stop_current_request()
                handle = session.generate_reply(user_input=event.text)
                self.bind_speech(handle, request)
        finally:
            _binding.reset(token)
