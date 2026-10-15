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
