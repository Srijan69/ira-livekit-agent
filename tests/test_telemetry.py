from types import SimpleNamespace

from telemetry import SessionStatusReporter


def test_reporter_emits_state_run_id_and_detail() -> None:
    lines: list[str] = []
    reporter = SessionStatusReporter(
        run_id="run-123", sink=lines.append, clock=lambda: 12.5
    )

    reporter.emit("LISTENING", "microphone ready")

    assert len(lines) == 1
    assert "run-123" in lines[0]
    assert "LISTENING" in lines[0]
    assert "microphone ready" in lines[0]


def test_reporter_never_raises_when_sink_fails() -> None:
    def broken_sink(_: str) -> None:
        raise OSError("terminal closed")

    reporter = SessionStatusReporter(sink=broken_sink)
    reporter.emit("ERROR", "ignored")


def test_child_reporter_shares_run_id_and_adds_label() -> None:
    lines: list[str] = []
    reporter = SessionStatusReporter(run_id="parent", sink=lines.append)

    reporter.child_run("worker").emit("RUNNING", "step one")

    assert "parent" in lines[0]
    assert "worker" in lines[0]


class TestStructuredObserver:
    """The second sink, added so the dashboard's orb can follow real states.

    The terminal path is untouched on purpose: these lines are diagnostics the
    project already relies on, and the orb needed structure rather than a
    formatted string. One reporter, two audiences.
    """

    def test_observer_receives_a_structured_event(self) -> None:
        events: list[dict] = []
        reporter = SessionStatusReporter(
            run_id="run-123", sink=lambda _: None, observer=events.append
        )

        reporter.emit("DELEGATING", "open_app")

        assert len(events) == 1
        assert events[0]["run_id"] == "run-123"
        assert events[0]["state"] == "DELEGATING"
        assert events[0]["detail"] == "open_app"
        assert isinstance(events[0]["at"], (int, float))

    def test_observer_and_terminal_both_fire(self) -> None:
        lines: list[str] = []
        events: list[dict] = []
        reporter = SessionStatusReporter(sink=lines.append, observer=events.append)

        reporter.emit("SPEAKING")

        assert len(lines) == 1
        assert len(events) == 1

    def test_a_broken_observer_never_breaks_the_session(self) -> None:
        """The orb going stale is survivable. A dropped turn is not.

        This observer publishes over the network, so it has more ways to fail
        than `print` does -- and none of them may reach the agent.
        """
        lines: list[str] = []

        def broken(_: dict) -> None:
            raise RuntimeError("room is gone")

        reporter = SessionStatusReporter(sink=lines.append, observer=broken)
        reporter.emit("LISTENING")

        # The terminal line still got out.
        assert len(lines) == 1

    def test_child_runs_inherit_the_observer(self) -> None:
        events: list[dict] = []
        reporter = SessionStatusReporter(
            run_id="parent", sink=lambda _: None, observer=events.append
        )

        reporter.child_run("lightweight").emit("RUNNING", "step 1/3")

        assert events[0]["run_id"] == "parent"
        assert events[0]["label"] == "lightweight"

    def test_state_is_normalized_to_upper_case(self) -> None:
        # The orb matches on state names; "running" and "RUNNING" arriving
        # from different call sites would silently miss.
        events: list[dict] = []
        reporter = SessionStatusReporter(sink=lambda _: None, observer=events.append)

        reporter.emit("running", "step")

        assert events[0]["state"] == "RUNNING"


def test_tool_reporters_keep_interleaved_calls_separate() -> None:
    events: list[dict] = []
    reporter = SessionStatusReporter(
        run_id="session-1", sink=lambda _: None, observer=events.append
    )
    first = reporter.for_tool(
        SimpleNamespace(
            function_call=SimpleNamespace(call_id="call-a", name="open_app")
        )
    )
    second = reporter.for_tool(
        SimpleNamespace(
            function_call=SimpleNamespace(call_id="call-b", name="read_page")
        )
    )

    first.emit("RUNNING", "desktop worker")
    second.emit("RUNNING", "browser worker")
    first.emit("DONE", "open_app")
    reporter.emit("LISTENING")

    assert [
        (e.get("call_id"), e.get("tool_name"), e.get("source")) for e in events
    ] == [
        ("call-a", "open_app", "tool"),
        ("call-b", "read_page", "tool"),
        ("call-a", "open_app", "tool"),
        (None, None, None),
    ]
    assert all(event["run_id"] == "session-1" for event in events)


def test_worker_child_keeps_enclosing_tool_metadata() -> None:
    events: list[dict] = []
    reporter = SessionStatusReporter(sink=lambda _: None, observer=events.append)
    bound = reporter.for_tool(
        SimpleNamespace(
            function_call=SimpleNamespace(call_id="workflow-1", name="run_workflow")
        )
    )

    bound.child_run("lightweight").emit("VERIFYING", "step 1/3")

    assert events[0]["label"] == "lightweight"
    assert events[0]["call_id"] == "workflow-1"
    assert events[0]["tool_name"] == "run_workflow"
    assert events[0]["source"] == "tool"


def test_standalone_tool_reporting_does_not_invent_call_metadata() -> None:
    events: list[dict] = []
    reporter = SessionStatusReporter(sink=lambda _: None, observer=events.append)

    reporter.for_tool(None).emit("RUNNING", "open_app")

    assert events[0]["state"] == "RUNNING"
    assert not {"call_id", "tool_name", "source"}.intersection(events[0])


def test_skill_progress_retains_session_call_and_zero_step() -> None:
    events: list[dict] = []
    reporter = SessionStatusReporter(
        run_id="session-1", sink=lambda _: None, observer=events.append
    ).for_tool(
        SimpleNamespace(
            function_call=SimpleNamespace(call_id="call-a", name="run_skill")
        )
    )
    reporter.child_run("skill").emit(
        "ERROR",
        source="skill",
        skill_run_id="skill-1",
        skill_slug="open-notes",
        skill_version=2,
        step_index=0,
        step_count=3,
        outcome="uncertain",
    )
    assert events[0] | {"at": 0} == {
        "run_id": "session-1",
        "call_id": "call-a",
        "tool_name": "run_skill",
        "label": "skill",
        "state": "ERROR",
        "detail": "",
        "at": 0,
        "source": "skill",
        "skill_run_id": "skill-1",
        "skill_slug": "open-notes",
        "skill_version": 2,
        "step_index": 0,
        "step_count": 3,
        "outcome": "uncertain",
    }
    assert events[0]["at"] > 1_000_000_000_000
