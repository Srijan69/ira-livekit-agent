import pytest

from telemetry import SessionStatusReporter
from workers import ToolRegistry, WorkflowRouter
from workflows import Workflow


@pytest.mark.asyncio
async def test_lightweight_worker_runs_declared_steps_in_order() -> None:
    calls: list[str] = []

    async def open_app(app_name: str) -> str:
        calls.append(app_name)
        return "opened"

    workflow = Workflow(
        "focus",
        "Focus",
        "Focus",
        ["open_app: app_name=Spotify"],
        allowed_tools=["open_app"],
    )
    result = await WorkflowRouter().run(
        workflow,
        ToolRegistry({"open_app": open_app}),
        SessionStatusReporter(sink=lambda _: None),
    )

    assert result.status == "done"
    assert calls == ["Spotify"]


@pytest.mark.asyncio
async def test_worker_stops_at_first_failed_step() -> None:
    calls: list[str] = []

    async def fail() -> str:
        calls.append("fail")
        raise RuntimeError("nope")

    async def later() -> str:
        calls.append("later")
        return "done"

    workflow = Workflow(
        "x",
        "X",
        "X",
        ["fail:", "later:"],
        allowed_tools=["fail", "later"],
    )
    result = await WorkflowRouter().run(
        workflow,
        ToolRegistry({"fail": fail, "later": later}),
        SessionStatusReporter(sink=lambda _: None),
    )

    assert result.status == "error"
    assert result.failed_step == 1
    assert calls == ["fail"]


@pytest.mark.asyncio
async def test_complex_workflow_uses_powerful_worker() -> None:
    class Recording:
        async def run(self, workflow, registry, reporter):
            return type("Result", (), {"status": "done"})()

    powerful = Recording()
    workflow = Workflow("long", "Long", "Long", ["x:"] * 6)
    result = await WorkflowRouter(powerful=powerful).run(
        workflow,
        ToolRegistry({"x": lambda: "ok"}),
        SessionStatusReporter(sink=lambda _: None),
    )
    assert result.status == "done"
