"""Strict workflow workers with a shared, testable execution contract."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable

from telemetry import SessionStatusReporter
from workflows import Workflow, classify_workflow


@dataclass(frozen=True)
class ExecutionResult:
    status: str
    workflow: str
    completed_steps: int
    total_steps: int
    failed_step: int | None = None
    message: str = ""


class ToolRegistry:
    def __init__(self, tools: dict[str, Callable[..., Any]] | None = None) -> None:
        self._tools = dict(tools or {})

    def names(self) -> set[str]:
        return set(self._tools)

    async def call(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        fn = self._tools.get(name)
        if fn is None:
            raise KeyError(f"tool '{name}' is not available")
        result = fn(**(arguments or {}))
        if inspect.isawaitable(result):
            result = await result
        return str(result)


def _step_action(step: str) -> tuple[str, dict[str, Any]]:
    """Read an explicit ``action: key=value`` prefix from a workflow step."""
    text = " ".join((step or "").split())
    if not text:
        raise ValueError("workflow step is empty")
    if ":" not in text:
        raise ValueError("step must declare an action with 'action:'")
    action, raw = text.split(":", 1)
    action = action.strip().lower().replace(" ", "_")
    arguments: dict[str, Any] = {}
    for item in raw.split(","):
        if "=" in item:
            key, value = item.split("=", 1)
            arguments[key.strip()] = value.strip()
    return action, arguments


class LightweightWorker:
    async def run(
        self,
        workflow: Workflow,
        registry: ToolRegistry,
        reporter: SessionStatusReporter,
    ) -> ExecutionResult:
        total = len(workflow.steps)
        completed = 0
        for number, step in enumerate(workflow.steps, 1):
            reporter.emit("RUNNING", f"step {number}/{total}")
            try:
                action, arguments = _step_action(step)
                if workflow.allowed_tools and action not in workflow.allowed_tools:
                    raise PermissionError(f"step uses undeclared tool '{action}'")
                result = await registry.call(action, arguments)
                reporter.emit("VERIFYING", f"step {number}/{total}")
                if not result.strip():
                    raise RuntimeError("tool returned no verification result")
            except Exception as exc:
                reporter.emit("ERROR", f"step {number}/{total}: {exc}")
                return ExecutionResult(
                    "error", workflow.name, completed, total, number, str(exc)
                )
            completed += 1
        reporter.emit("DONE", f"{completed}/{total} steps")
        return ExecutionResult("done", workflow.name, completed, total)


class PowerfulWorker(LightweightWorker):
    """Conservative powerful-worker boundary.

    The worker still executes declared actions only. A future model strategy
    can choose among those actions, but it cannot invent a desktop command.
    """


class WorkflowRouter:
    def __init__(
        self,
        lightweight: LightweightWorker | None = None,
        powerful: PowerfulWorker | None = None,
    ) -> None:
        self.lightweight = lightweight or LightweightWorker()
        self.powerful = powerful or PowerfulWorker()

    async def run(
        self,
        workflow: Workflow,
        registry: ToolRegistry,
        reporter: SessionStatusReporter,
    ) -> ExecutionResult:
        worker_name = classify_workflow(workflow)
        reporter.emit("DELEGATING", f"{worker_name} worker")
        worker = self.powerful if worker_name == "powerful" else self.lightweight
        return await worker.run(workflow, registry, reporter.child_run(worker_name))
