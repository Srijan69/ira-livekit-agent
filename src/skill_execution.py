"""Deterministic skill execution with observations and durable delivery identity.

The LLM selects a procedure; this module owns validation, execution order,
cancellation, deadlines, and the truth of its result. A nonempty sentence is
never evidence of successful work.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import math
import re
import time
import types
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

import psutil

from skill_format import CHECK_KINDS, Action, Check, Skill, render_skill
from skill_runs import TERMINAL_STATUSES, SkillRunStore
from skill_store import skill_slug as canonical_skill_slug
from telemetry import SessionStatusReporter

_PARAM = re.compile(r"\{([A-Za-z_]\w*)\}")


@dataclass(frozen=True)
class ToolOutcome:
    ok: bool
    message: str = ""
    evidence: str = ""

    def __post_init__(self) -> None:
        if (
            type(self.ok) is not bool
            or not isinstance(self.message, str)
            or not isinstance(self.evidence, str)
        ):
            raise TypeError(
                "ToolOutcome requires boolean ok and string message/evidence"
            )


@dataclass(frozen=True)
class CheckOutcome:
    ok: bool
    evidence: str = ""

    def __post_init__(self) -> None:
        if type(self.ok) is not bool or not isinstance(self.evidence, str):
            raise TypeError("CheckOutcome requires boolean ok and string evidence")


class UnverifiedToolOutcomeError(ValueError):
    """The action returned no machine-readable statement of its outcome."""


def _typed_value(value: Any, annotation: Any) -> Any:
    if annotation in (inspect.Parameter.empty, Any):
        if type(value) not in (str, int, float, bool, type(None)):
            raise ValueError("arguments must be scalar values")
        return value
    origin = get_origin(annotation)
    if origin in (Union, types.UnionType):
        for alternative in get_args(annotation):
            try:
                return _typed_value(value, alternative)
            except ValueError:
                pass
        raise ValueError("value does not match a permitted type")
    if origin is Literal:
        if value not in get_args(annotation):
            raise ValueError(f"expected one of {get_args(annotation)}")
        return value
    if annotation is str and isinstance(value, str):
        return value
    if annotation is bool:
        if type(value) is bool:
            return value
        if isinstance(value, str) and value.lower() in ("true", "false"):
            return value.lower() == "true"
    if annotation is int:
        if type(value) is int:
            return value
        if isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value):
            return int(value)
    if annotation is float:
        try:
            number = float(value) if type(value) in (str, int, float) else math.nan
            if math.isfinite(number):
                return number
        except ValueError:
            pass
    if annotation is type(None) and value is None:
        return None
    raise ValueError(f"invalid {getattr(annotation, '__name__', annotation)} value")


class ToolRegistry:
    """An explicit capability list with parameter binding before execution.

    Optional validators perform semantic checks (e.g. URL schemes) without
    effects. Both callable signatures and validators are applied to every
    step in preflight, before the first desktop operation.
    """

    def __init__(
        self,
        tools: dict[str, Callable[..., Any]] | None = None,
        *,
        validators: dict[str, Callable[..., None]] | None = None,
    ) -> None:
        self._tools = dict(tools or {})
        self._validators = dict(validators or {})

    def names(self) -> set[str]:
        return set(self._tools)

    def validate(
        self,
        name: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        symbolic: bool | frozenset[str] = False,
    ) -> dict[str, Any]:
        fn = self._tools.get(name)
        if fn is None:
            raise ValueError(f"tool '{name}' is not available")
        args = dict(arguments or {})
        try:
            signature = inspect.signature(fn)
            signature.bind(**args)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"invalid arguments for '{name}': {exc}") from exc
        try:
            annotations = get_type_hints(fn)
        except (NameError, TypeError):
            annotations = {}
        unresolved: set[str] = set()
        for key, value in args.items():
            parameter = signature.parameters.get(key)
            # **kwargs does not grant undeclared parameters at this boundary.
            if parameter is None or parameter.kind in (
                inspect.Parameter.VAR_KEYWORD,
                inspect.Parameter.VAR_POSITIONAL,
            ):
                raise ValueError(f"undeclared argument '{key}' for '{name}'")
            if (
                symbolic is True and isinstance(value, str) and _PARAM.search(value)
            ) or (not isinstance(symbolic, bool) and key in symbolic):
                unresolved.add(key)
                continue
            annotation = annotations.get(key, parameter.annotation)
            try:
                args[key] = _typed_value(value, annotation)
            except ValueError as exc:
                raise ValueError(
                    f"invalid argument '{key}' for '{name}': {exc}"
                ) from exc
        validator = self._validators.get(name)
        if validator is not None:
            if "_symbolic" in inspect.signature(validator).parameters:
                validator(**args, _symbolic=frozenset(unresolved))
            elif not unresolved:
                validator(**args)
            elif set(args) - unresolved:
                raise ValueError(
                    f"validator for '{name}' cannot preflight mixed symbolic arguments"
                )
        return args

    async def call(
        self, name: str, arguments: Mapping[str, Any] | None = None
    ) -> ToolOutcome:
        args = self.validate(name, arguments)
        fn = self._tools[name]
        if inspect.iscoroutinefunction(fn):
            result = await fn(**args)
        else:
            # A sync desktop operation must not freeze voice/cancellation.
            result = await asyncio.to_thread(fn, **args)
            if inspect.isawaitable(result):
                result = await result
        if not isinstance(result, ToolOutcome):
            raise UnverifiedToolOutcomeError(
                "unverified tool outcome: expected ToolOutcome, not prose"
            )
        return result


@dataclass(frozen=True)
class SkillExecutionResult:
    status: str
    run_id: str
    skill_slug: str
    skill_name: str
    skill_version: int
    completed_steps: int
    total_steps: int
    failed_step: int | None = None
    message: str = ""
    verified: bool = False
    question: str = ""
    question_id: str = ""
    input_name: str = ""

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> SkillExecutionResult:
        return cls(
            **{
                key: record.get(key, field.default)
                for key, field in cls.__dataclass_fields__.items()
            }
        )


@dataclass(frozen=True)
class PreparedStep:
    index: int
    action: Action
    arguments: tuple[tuple[str, Any], ...]
    check: Check | None


@dataclass(frozen=True)
class PreparedSkill:
    skill: Skill
    steps: tuple[PreparedStep, ...]
    done_check: Check | None


@dataclass
class _Question:
    question_id: str
    input_name: str
    deadline: float
    validate_answer: Callable[[str], PreparedSkill]
    answered: asyncio.Event = field(default_factory=asyncio.Event)
    plan: PreparedSkill | None = None
    answer: str = ""


@dataclass
class _Control:
    stop: asyncio.Event
    deadline: float
    progress: asyncio.Future
    question: _Question | None = None
    has_answers: bool = False


class _HaltError(Exception):
    def __init__(self, status: str, message: str, pending: asyncio.Task | None = None):
        super().__init__(message)
        self.status = status
        self.pending = pending


class SkillExecutionService:
    def __init__(
        self,
        *,
        verifier: Callable[[Check], Any],
        run_store: SkillRunStore | None = None,
        action_timeout: float = 30,
        run_timeout: float = 120,
        clarification_timeout: float = 120,
        execution_lock: asyncio.Lock | None = None,
        poll_interval: float = 0.1,
        supported_checks: tuple[str, ...] = CHECK_KINDS,
        check_validator: Callable[..., None] | None = None,
    ) -> None:
        for value in (
            action_timeout,
            run_timeout,
            clarification_timeout,
            poll_interval,
        ):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("execution deadlines must be positive and finite")
        self.verifier = verifier
        self.run_store = run_store if run_store is not None else SkillRunStore()
        self.run_store.recover_interrupted()
        self.action_timeout = action_timeout
        self.run_timeout = run_timeout
        self.clarification_timeout = clarification_timeout
        self.poll_interval = poll_interval
        self.supported_checks = frozenset(supported_checks)
        self.check_validator = check_validator
        self.execution_lock = (
            execution_lock if execution_lock is not None else asyncio.Lock()
        )
        self._tasks: dict[str, asyncio.Task] = {}
        self._controls: dict[str, _Control] = {}
        self._drains: set[asyncio.Task] = set()
        owner = psutil.Process()
        self._owner_pid = owner.pid
        self._owner_created_at = owner.create_time()
        self._closed = False

    def validate(
        self,
        skill: Skill,
        registry: ToolRegistry,
        inputs: Mapping[str, Any] | None = None,
        *,
        require_enabled: bool = True,
        _answers: Mapping[int, str] | None = None,
    ) -> PreparedSkill:
        if require_enabled and skill.state != "enabled":
            raise ValueError("skill must be explicitly enabled before execution")
        if not skill.steps or len(skill.steps) > 100:
            raise ValueError("a skill requires between 1 and 100 steps")
        if type(skill.version) is not int or skill.version < 1:
            raise ValueError("skill version must be a positive integer")
        if skill.preconditions:
            raise ValueError(
                "unsupported prose preconditions; encode them as initial check steps"
            )
        if skill.done_when and skill.done_check is None:
            raise ValueError("Done when requires an explicit observable check")
        allowed = set(skill.allowed_tools)
        if not allowed <= registry.names():
            raise ValueError("allowed_tools includes unavailable tools")
        declared = {item.name: item for item in skill.inputs}
        if len(declared) != len(skill.inputs):
            raise ValueError("duplicate declared inputs")
        provided = dict(inputs or {})
        if "playlist" in declared and "playlist" not in provided:
            for alt in ("name", "playlist_name", "title"):
                if alt in provided:
                    provided["playlist"] = provided.pop(alt)
                    break
        if "app" in declared and "app" not in provided:
            for alt in ("name", "app_name", "application"):
                if alt in provided:
                    provided["app"] = provided.pop(alt)
                    break
        if "url" in declared and "url" not in provided:
            for alt in ("address", "link", "page"):
                if alt in provided:
                    provided["url"] = provided.pop(alt)
                    break
        if provided.keys() - declared.keys():
            raise ValueError(
                "unknown skill inputs: "
                + ", ".join(sorted(provided.keys() - declared.keys()))
            )
        values = {name: item.default for name, item in declared.items()}
        values.update(provided)
        asks = {
            step.action.param
            for step in skill.steps
            if step.action is not None and step.action.kind == "ask"
        }
        for name, value in values.items():
            if value is None:
                if require_enabled and name not in asks:
                    raise ValueError(f"missing required input '{name}'")
            elif type(value) not in (str, int, float, bool):
                raise ValueError(f"input '{name}' must be a scalar value")

        available = {name for name, value in values.items() if value is not None}
        private_inputs: set[str] = set()

        def substitute(text: str, *, public: bool = False) -> str:
            def bound(match: re.Match) -> str:
                name = match.group(1)
                if name not in declared:
                    raise ValueError(f"undeclared input '{{{name}}}'")
                if require_enabled and name not in available:
                    raise ValueError(f"input '{name}' is used before its ask step")
                value = values[name]
                if public and name in private_inputs:
                    return "[redacted]"
                return match.group(0) if value is None else str(value)

            return _PARAM.sub(bound, text)

        def symbolic_names(text: str) -> frozenset[str]:
            return frozenset(
                name for name in _PARAM.findall(text) if values.get(name) is None
            )

        def checked(check: Check | None) -> Check | None:
            if check is None:
                return None
            if check.kind not in self.supported_checks:
                raise ValueError(f"unsupported check '{check.kind}'")
            if check.kind == "page_value":
                try:
                    fields = json.loads(check.value)
                except (ValueError, TypeError):
                    raise ValueError(
                        "page_value requires a JSON selector/value object"
                    ) from None
                if (
                    not isinstance(fields, dict)
                    or set(fields) != {"selector", "value"}
                    or not all(isinstance(value, str) for value in fields.values())
                ):
                    raise ValueError(
                        "page_value requires string selector and value fields"
                    )
                symbolic = frozenset(
                    key for key, text in fields.items() if symbolic_names(text)
                )
                value = json.dumps(
                    {key: substitute(text) for key, text in fields.items()},
                    ensure_ascii=False,
                )
            elif check.kind == "pointer_position":
                try:
                    fields = json.loads(check.value)
                except (ValueError, TypeError):
                    raise ValueError(
                        "pointer_position requires a JSON x/y object"
                    ) from None
                if not isinstance(fields, dict) or set(fields) != {"x", "y"}:
                    raise ValueError("pointer_position requires x and y coordinates")
                symbolic = frozenset(
                    key
                    for key, text in fields.items()
                    if isinstance(text, str) and symbolic_names(text)
                )
                coordinates = {}
                for key, coordinate in fields.items():
                    resolved = (
                        substitute(coordinate)
                        if isinstance(coordinate, str)
                        else coordinate
                    )
                    coordinates[key] = (
                        resolved if key in symbolic else _typed_value(resolved, int)
                    )
                value = json.dumps(coordinates)
            else:
                symbolic = (
                    frozenset({"value"}) if symbolic_names(check.value) else frozenset()
                )
                value = substitute(check.value)
            if not value.strip():
                raise ValueError("check value must not be empty")
            if check.within is not None and (
                type(check.within) not in (int, float)
                or not math.isfinite(check.within)
                or check.within <= 0
                or check.within > 600
            ):
                raise ValueError("check deadline must be between 0 and 600 seconds")
            result = replace(check, value=value)
            if self.check_validator is not None:
                self.check_validator(result, symbolic=symbolic)
            return result

        prepared = []
        for index, step in enumerate(skill.steps, 1):
            action = step.action
            if action is None:
                raise ValueError(f"step {index} has no compiled action")
            # An ask question reads only bindings already available; its
            # optional observation is evaluated after the accepted answer.
            if action.kind == "ask":
                if action.param not in declared:
                    raise ValueError(f"step {index} asks for an undeclared input")
                if not isinstance(action.text, str) or not action.text.strip():
                    raise ValueError(f"step {index} has an empty question")
                action = replace(action, text=substitute(action.text, public=True))
                if len(action.text) > 1000 or "\x00" in action.text:
                    raise ValueError(
                        f"step {index} question exceeds its size or character bounds"
                    )
                values[action.param] = (_answers or {}).get(index)
                available.add(action.param)
                if index in (_answers or {}):
                    private_inputs.add(action.param)
            action = replace(
                action, check=checked(action.check), until=checked(action.until)
            )
            check = checked(step.check)
            arguments: dict[str, Any] = {}
            if action.kind == "tool":
                if action.tool not in allowed:
                    raise ValueError(
                        f"step {index} uses undeclared tool '{action.tool}'"
                    )
                if check is None:
                    raise ValueError(f"step {index} needs an observable check")
                if len(dict(action.args)) != len(action.args):
                    raise ValueError(f"step {index} has duplicate arguments")
                arguments = registry.validate(
                    action.tool,
                    {name: substitute(value) for name, value in action.args},
                    symbolic=frozenset(
                        name for name, value in action.args if symbolic_names(value)
                    ),
                )
            elif action.kind == "check":
                if action.check is None:
                    raise ValueError(f"step {index} has no check condition")
            elif action.kind == "wait":
                if action.until is None and (
                    type(action.seconds) not in (int, float)
                    or not math.isfinite(action.seconds)
                    or not 0 <= action.seconds <= 600
                ):
                    raise ValueError("wait duration must be between 0 and 600 seconds")
            elif action.kind == "ask":
                pass
            else:
                raise ValueError(
                    f"unsupported action '{action.kind}'; resolve voice interaction before execution"
                )
            prepared.append(
                PreparedStep(index, action, tuple(arguments.items()), check)
            )
        done_check = checked(skill.done_check)
        if not done_check and not any(
            step.check or step.action.check or step.action.until for step in prepared
        ):
            raise ValueError("skill requires at least one observable check")
        return PreparedSkill(skill, tuple(prepared), done_check)

    def status(self, run_id: str) -> dict[str, Any] | None:
        return self.run_store.get(run_id)

    async def wait_for_terminal(self, run_id: str) -> SkillExecutionResult:
        """Observe coordinator completion without taking cancellation ownership."""
        task = self._tasks.get(run_id)
        if task is not None:
            return await asyncio.shield(task)
        record = await asyncio.to_thread(self.run_store.get, run_id)
        if record is None or record["status"] not in TERMINAL_STATUSES:
            raise ValueError("No locally owned active or terminal skill run")
        return SkillExecutionResult.from_record(record)

    def stop(self, run_id: str) -> bool:
        control = self._controls.get(run_id)
        if control is None:
            return False
        control.stop.set()
        return True

    async def answer(
        self, run_id: str, answer: str, *, question_id: str
    ) -> SkillExecutionResult:
        """Accept one answer for the current prompt and yield at the next boundary.

        Validation and claiming the question happen without an await, so two
        deliveries cannot bind the same prompt or advance a later prompt.
        Answers live only in the coordinator's in-memory plan.
        """
        if self._closed:
            raise ValueError("Skill execution service is closed")
        control = self._controls.get(run_id)
        question = control.question if control is not None else None
        if control is None or question is None or control.stop.is_set():
            raise ValueError("Skill run is not waiting for an answer")
        if not question_id or question.question_id != question_id:
            raise ValueError("Question is stale or does not belong to this run")
        if time.monotonic() >= min(control.deadline, question.deadline):
            raise ValueError("Clarification deadline exceeded")
        if (
            not isinstance(answer, str)
            or not answer.strip()
            or len(answer) > 4000
            or "\x00" in answer
        ):
            raise ValueError("Answer must contain 1 to 4000 characters without NUL")
        try:
            plan = question.validate_answer(answer)
        except Exception:
            # Validator exceptions can echo the supplied text; never return
            # that text through tool errors or persist it in the run ledger.
            raise ValueError(
                "Answer is invalid for the remaining skill steps; please retry"
            ) from None
        if control.stop.is_set() or time.monotonic() >= min(
            control.deadline, question.deadline
        ):
            raise ValueError("Clarification expired or was stopped during validation")
        control.progress = asyncio.get_running_loop().create_future()
        control.has_answers = True
        control.question = None
        question.plan = plan
        question.answer = answer
        question.answered.set()
        return await self._await_progress(run_id, control)

    async def _await_progress(
        self, run_id: str, control: _Control
    ) -> SkillExecutionResult:
        try:
            return await asyncio.shield(control.progress)
        except asyncio.CancelledError:
            self.stop(run_id)
            task = self._tasks.get(run_id)
            if task is not None:
                await asyncio.shield(task)
            raise

    async def _coordinate(self, run_id: str, control: _Control, *args):
        try:
            result = await self._execute(run_id, *args, control=control)
            if not control.progress.done():
                control.progress.set_result(result)
            return result
        except BaseException as exc:
            if not control.progress.done():
                control.progress.set_exception(exc)
            raise
        finally:
            control.question = None
            self._tasks.pop(run_id, None)
            self._controls.pop(run_id, None)

    async def aclose(self) -> None:
        """Stop coordinators promptly, retaining ownership of pending effects."""
        self._closed = True
        for control in self._controls.values():
            control.stop.set()
        tasks = tuple(self._tasks.values())
        if tasks:
            await asyncio.gather(*(asyncio.shield(task) for task in tasks))

    async def run(
        self,
        skill: Skill,
        registry: ToolRegistry,
        reporter: SessionStatusReporter,
        *,
        inputs: Mapping[str, Any] | None = None,
        run_id: str | None = None,
        skill_slug: str | None = None,
    ) -> SkillExecutionResult:
        if self._closed:
            raise ValueError("Skill execution service is closed")
        if skill_slug is not None:
            if not isinstance(skill_slug, str) or not re.fullmatch(
                r"[A-Za-z0-9](?:[A-Za-z0-9 _.-]*[A-Za-z0-9_-])?", skill_slug
            ):
                raise ValueError("skill slug must be a portable filename stem")
            canonical_skill_slug(skill_slug)  # Reject reserved Windows names.
        run_id = run_id or uuid.uuid4().hex
        control = self._controls.get(run_id)
        if control is None:
            control = _Control(
                asyncio.Event(),
                time.monotonic() + self.run_timeout,
                asyncio.get_running_loop().create_future(),
            )
            self._controls[run_id] = control
            task = asyncio.create_task(
                self._coordinate(
                    run_id, control, skill, registry, reporter, inputs, skill_slug
                )
            )
            self._tasks[run_id] = task
            # Exceptions are delivered through the progress future. Retrieving
            # the coordinator exception also avoids unobserved-task warnings.
            task.add_done_callback(
                lambda done: None if done.cancelled() else done.exception()
            )
        return await self._await_progress(run_id, control)

    async def _acquire(self, control: _Control) -> None:
        acquire = asyncio.create_task(self.execution_lock.acquire())
        stopped = asyncio.create_task(control.stop.wait())
        try:
            await asyncio.wait(
                (acquire, stopped),
                timeout=max(0, control.deadline - time.monotonic()),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if control.stop.is_set() or time.monotonic() >= control.deadline:
                if acquire.done() and not acquire.cancelled() and acquire.result():
                    self.execution_lock.release()
                else:
                    acquire.cancel()
                    await asyncio.gather(acquire, return_exceptions=True)
                raise _HaltError(
                    "cancelled" if control.stop.is_set() else "error",
                    "Stopped before execution"
                    if control.stop.is_set()
                    else "Run deadline exceeded before execution",
                )
            await acquire
        finally:
            stopped.cancel()
            await asyncio.gather(stopped, return_exceptions=True)

    async def _guard(
        self,
        operation,
        control: _Control,
        *,
        timeout: float,
        effect: bool = False,
        retain: bool = True,
    ):
        # Check before scheduling: a zero-second wait can still let the newly
        # created task issue a desktop action before reporting its timeout.
        if (
            control.stop.is_set()
            or time.monotonic() >= control.deadline
            or timeout <= 0
        ):
            operation.close()
            raise _HaltError(
                "cancelled" if control.stop.is_set() else "error",
                "Execution stopped before the next action"
                if control.stop.is_set()
                else "Execution deadline exceeded before the next action",
            )
        pending = asyncio.create_task(operation)
        stopped = asyncio.create_task(control.stop.wait())
        try:
            await asyncio.wait(
                (pending, stopped),
                timeout=max(0, min(timeout, control.deadline - time.monotonic())),
                return_when=asyncio.FIRST_COMPLETED,
            )
            stopping = control.stop.is_set()
            if pending.done() and not stopping:
                try:
                    return await pending
                except UnverifiedToolOutcomeError:
                    raise
                except asyncio.CancelledError as exc:
                    raise _HaltError(
                        "uncertain" if effect else "error",
                        "Operation was cancelled before its outcome was verified",
                    ) from exc
                except Exception as exc:
                    if effect:
                        raise _HaltError(
                            "uncertain", f"Action outcome is uncertain: {exc}"
                        ) from exc
                    raise
            if pending.done():
                await asyncio.gather(pending, return_exceptions=True)
                raise _HaltError("cancelled", "Execution stopped")
            if not retain:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
                pending = None
            status = "uncertain" if effect else ("cancelled" if stopping else "error")
            message = "Execution stopped" if stopping else "Execution deadline exceeded"
            if effect:
                message += "; the in-flight action may have taken effect and will not be retried"
            raise _HaltError(status, message, pending)
        finally:
            stopped.cancel()
            await asyncio.gather(stopped, return_exceptions=True)

    async def _observe(self, check: Check) -> CheckOutcome:
        if inspect.iscoroutinefunction(self.verifier):
            outcome = await self.verifier(check)
        else:
            outcome = await asyncio.to_thread(self.verifier, check)
            if inspect.isawaitable(outcome):
                outcome = await outcome
        if isinstance(outcome, CheckOutcome):
            return outcome
        if isinstance(outcome, ToolOutcome):
            return CheckOutcome(outcome.ok, outcome.evidence)
        if type(outcome) is bool:
            return CheckOutcome(outcome, f"{check.kind}: {check.value} = {outcome}")
        if isinstance(outcome, tuple) and len(outcome) == 2:
            return CheckOutcome(*outcome)
        raise ValueError(
            "unverified check outcome: expected boolean observation/evidence"
        )

    async def _check(self, check: Check, control: _Control) -> CheckOutcome:
        deadline = min(
            control.deadline, time.monotonic() + (check.within or self.action_timeout)
        )
        while True:
            result = await self._guard(
                self._observe(check),
                control,
                timeout=min(self.action_timeout, max(0, deadline - time.monotonic())),
            )
            if result.ok or check.within is None or time.monotonic() >= deadline:
                return result
            await self._guard(
                asyncio.sleep(
                    min(self.poll_interval, max(0, deadline - time.monotonic()))
                ),
                control,
                timeout=self.poll_interval + 0.01,
                retain=False,
            )
            if time.monotonic() >= deadline:
                return result

    async def _drain_and_release(self, pending: asyncio.Task) -> None:
        try:
            await asyncio.shield(pending)
        except (Exception, asyncio.CancelledError):
            pass
        finally:
            self.execution_lock.release()

    async def _execute(
        self, run_id, skill, registry, reporter, inputs, stored_slug=None, *, control
    ) -> SkillExecutionResult:
        existing = await asyncio.to_thread(self.run_store.get, run_id)
        if existing is not None:
            return SkillExecutionResult.from_record(existing)
        slug = stored_slug or canonical_skill_slug(skill.name)
        try:
            markdown = render_skill(skill)
        except (ValueError, TypeError, AttributeError):
            markdown = ""
        record = {
            "run_id": run_id,
            "skill_slug": slug,
            "skill_name": skill.name,
            "skill_version": skill.version,
            "status": "queued",
            "completed_steps": 0,
            "total_steps": len(skill.steps),
            "failed_step": None,
            "message": "",
            "verified": False,
            "question": "",
            "question_id": "",
            "input_name": "",
            "started_at": time.time() * 1000,
            "finished_at": None,
            "steps": [],
            "skill_markdown": markdown,
            "done_evidence": "",
            "owner_pid": self._owner_pid,
            "owner_created_at": self._owner_created_at,
        }
        if not await asyncio.to_thread(self.run_store.create, record):
            existing = await asyncio.to_thread(self.run_store.get, run_id)
            return SkillExecutionResult.from_record(existing)

        def emit(
            state,
            detail="",
            *,
            index=None,
            outcome=None,
            step_tool=None,
            step_outcome=None,
        ):
            reporter.emit(
                state,
                detail,
                source="skill",
                skill_run_id=run_id,
                skill_slug=slug,
                skill_version=skill.version,
                step_index=index if index is not None else 0,
                step_count=len(skill.steps),
                step_tool=step_tool,
                step_outcome=step_outcome,
                outcome=outcome,
            )

        acquired = False
        pending = None
        completed = 0
        current = None
        step_record = None
        answers: dict[int, str] = {}
        last_check = None
        status, message = "error", "Execution did not complete"

        def private_text(value: str) -> str:
            return "[redacted]" if control.has_answers and value else value

        try:
            # Keep the initial input mapping frozen even if the caller later
            # mutates its original dictionary while the run is paused.
            inputs = dict(inputs or {})
            plan = self.validate(skill, registry, inputs)
            emit("DELEGATING", skill.name)
            await self._acquire(control)
            acquired = True
            await asyncio.to_thread(self.run_store.update, run_id, status="running")
            while completed < len(plan.steps):
                prepared = plan.steps[completed]
                if control.stop.is_set():
                    raise _HaltError(
                        "cancelled", "Execution stopped before the next step"
                    )
                current = prepared.index
                action = prepared.action
                emit(
                    "RUNNING",
                    f"step {current}/{len(plan.steps)}",
                    index=current,
                    step_tool=action.tool or action.kind,
                    step_outcome="running",
                )
                step_record = {
                    "step_index": current,
                    "tool": action.tool or action.kind,
                    "args": {
                        key: "[redacted]" if control.has_answers else value
                        for key, value in prepared.arguments
                    },
                    "outcome": "running",
                    "message": "",
                    "evidence": "",
                    "check_evidence": "",
                    "started_at": time.time() * 1000,
                }
                await asyncio.to_thread(self.run_store.record_step, run_id, step_record)
                if action.kind == "ask":
                    # An answer can arrive through the persisted question ID
                    # before the waiting write returns. It replaces progress
                    # for the answer caller, so publish this boundary through
                    # the original caller's future captured before any awaits.
                    waiting_progress = control.progress

                    def validate_answer(value: str, index=current) -> PreparedSkill:
                        proposed = {**answers, index: value}
                        return self.validate(skill, registry, inputs, _answers=proposed)

                    question = _Question(
                        uuid.uuid4().hex,
                        action.param,
                        min(
                            control.deadline,
                            time.monotonic() + self.clarification_timeout,
                        ),
                        validate_answer,
                    )
                    control.question = question
                    self.execution_lock.release()
                    acquired = False
                    step_record["outcome"] = "waiting"
                    await asyncio.to_thread(
                        self.run_store.record_step, run_id, step_record
                    )
                    record = await asyncio.to_thread(
                        self.run_store.update,
                        run_id,
                        status="waiting",
                        question=action.text,
                        question_id=question.question_id,
                        input_name=question.input_name,
                        message="Waiting for clarification",
                    )
                    emit(
                        "LISTENING",
                        "Waiting for clarification",
                        index=current,
                        outcome="waiting",
                        step_tool=action.kind,
                        step_outcome="waiting",
                    )
                    if not waiting_progress.done():
                        waiting_progress.set_result(
                            SkillExecutionResult.from_record(record)
                        )
                    await self._guard(
                        question.answered.wait(),
                        control,
                        timeout=question.deadline - time.monotonic(),
                        retain=False,
                    )
                    plan = question.plan
                    answers[current] = question.answer
                    prepared = plan.steps[completed]
                    action = prepared.action
                    await self._acquire(control)
                    acquired = True
                    if last_check is not None:
                        emit(
                            "VERIFYING",
                            "Rechecking state after clarification",
                            index=current,
                            step_tool=action.kind,
                            step_outcome="verifying",
                        )
                        observation = await self._check(last_check, control)
                        if not observation.ok:
                            raise ValueError(
                                "Observed state changed while awaiting clarification"
                            )
                    await asyncio.to_thread(
                        self.run_store.update,
                        run_id,
                        status="running",
                        message="",
                        question="",
                        question_id="",
                        input_name="",
                    )
                elif action.kind == "tool":
                    result = await self._guard(
                        registry.call(action.tool, dict(prepared.arguments)),
                        control,
                        timeout=self.action_timeout,
                        effect=True,
                    )
                    step_record.update(
                        message=private_text(result.message)[:4000],
                        evidence=private_text(result.evidence)[:4000],
                    )
                    if not result.ok:
                        raise ValueError(result.message or "Tool reported failure")
                elif action.kind == "wait" and action.until is None:
                    await self._guard(
                        asyncio.sleep(action.seconds),
                        control,
                        timeout=action.seconds + 0.01,
                        retain=False,
                    )
                emit(
                    "VERIFYING",
                    f"step {current}/{len(plan.steps)}",
                    index=current,
                    step_tool=action.tool or action.kind,
                    step_outcome="verifying",
                )
                for check in (action.check, action.until, prepared.check):
                    if check is None:
                        continue
                    observation = await self._check(check, control)
                    step_record["check_evidence"] = private_text(observation.evidence)[
                        :4000
                    ]
                    if not observation.ok:
                        raise ValueError(f"Check failed: {check.kind} {check.value}")
                    last_check = check
                if control.stop.is_set():
                    raise _HaltError("cancelled", "Execution stopped")
                completed += 1
                step_record.update(outcome="done", finished_at=time.time() * 1000)
                await asyncio.to_thread(self.run_store.record_step, run_id, step_record)
                await asyncio.to_thread(
                    self.run_store.update, run_id, completed_steps=completed
                )
                emit(
                    "RUNNING",
                    f"step {current}/{len(plan.steps)}",
                    index=current,
                    step_tool=action.tool or action.kind,
                    step_outcome="done",
                )
            if plan.done_check:
                emit("VERIFYING", "final outcome", index=current)
                observation = await self._check(plan.done_check, control)
                await asyncio.to_thread(
                    self.run_store.update,
                    run_id,
                    done_evidence=private_text(observation.evidence)[:4000],
                )
                if not observation.ok:
                    raise ValueError("Final outcome check failed")
            if control.stop.is_set():
                raise _HaltError("cancelled", "Execution stopped")
            status, message = "done", f"Verified {completed}/{len(plan.steps)} steps"
        except _HaltError as exc:
            status, message, pending = exc.status, private_text(str(exc)), exc.pending
        except Exception as exc:
            status, message = "error", private_text(str(exc))
        finally:
            if acquired:
                if pending is not None and not pending.done():
                    drain = asyncio.create_task(self._drain_and_release(pending))
                    self._drains.add(drain)
                    drain.add_done_callback(self._drains.discard)
                else:
                    if pending is not None:
                        await asyncio.gather(pending, return_exceptions=True)
                    self.execution_lock.release()
        if step_record and status != "done" and step_record["outcome"] != "done":
            step_record.update(
                outcome=status, message=message[:4000], finished_at=time.time() * 1000
            )
            await asyncio.to_thread(self.run_store.record_step, run_id, step_record)
        record = await asyncio.to_thread(
            self.run_store.update,
            run_id,
            status=status,
            message=message[:4000],
            completed_steps=completed,
            failed_step=current if status != "done" else None,
            verified=status == "done",
            finished_at=time.time() * 1000,
            question="",
            question_id="",
            input_name="",
        )
        emit(
            "DONE" if record["status"] == "done" else "ERROR",
            # Driver exceptions stay in the run ledger. Publishing them can
            # exceed the dashboard transport limit and lose terminal truth.
            {
                "done": f"Verified {completed}/{len(skill.steps)} steps",
                "error": "Skill execution failed",
                "cancelled": "Skill execution cancelled",
                "uncertain": "Skill outcome is uncertain",
            }[record["status"]],
            index=current,
            outcome=record["status"],
            step_tool=(
                step_record["tool"]
                if step_record and step_record["outcome"] != "done"
                else None
            ),
            step_outcome=(
                step_record["outcome"]
                if step_record and step_record["outcome"] != "done"
                else None
            ),
        )
        return SkillExecutionResult.from_record(record)
