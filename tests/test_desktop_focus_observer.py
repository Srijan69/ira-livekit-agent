"""Focused UIA reads retain one process and clean up failed provider work."""

import io
import json
import subprocess
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from desktop import DesktopController
from desktop_accessibility import (
    FocusObservationError,
    FocusObserver,
    focused_control_observer,
)
from request_scope import ScopeViolation


def test_focus_reads_reuse_one_process_and_do_not_send_typed_text(monkeypatch):
    commands = io.StringIO()
    controls = [
        {
            "identity": "field-1",
            "name": "",
            "role": "Edit",
            "bounds": [0, 0, 10, 10],
            "has_keyboard_focus": True,
        },
        {
            "identity": "field-2",
            "name": "",
            "role": "Edit",
            "bounds": [0, 20, 10, 10],
            "has_keyboard_focus": True,
        },
    ]
    output = io.StringIO(
        "\n".join(
            json.dumps(item)
            for item in [
                {"ready": True},
                *({"control": control} for control in controls),
            ]
        )
        + "\n"
    )
    launches = []
    process = SimpleNamespace(
        stdin=commands, stdout=output, poll=lambda: None, wait=lambda **kwargs: 0
    )

    def launch(*args, **kwargs):
        launches.append((args, kwargs))
        return process

    monkeypatch.setattr("desktop_accessibility.subprocess.Popen", launch)
    with focused_control_observer() as snapshot:
        assert snapshot() == [controls[0]]
        assert snapshot() == [controls[1]]
        assert commands.getvalue() == "focus\nfocus\n"
    assert len(launches) == 1
    assert commands.closed and output.closed


def test_stale_uia_focus_record_cannot_authorize_input(monkeypatch):
    observer = FocusObserver()
    observer.process = SimpleNamespace(stdin=io.StringIO(), poll=lambda: None)
    monkeypatch.setattr(
        observer,
        "_read",
        lambda timeout: {
            "control": {"identity": "old-field", "has_keyboard_focus": False}
        },
    )
    with pytest.raises(FocusObservationError, match="unavailable"):
        observer.snapshot()


def test_failed_focus_startup_still_closes_its_process(monkeypatch):
    effects = []

    class Observer:
        def start(self):
            effects.append("started")
            raise RuntimeError("UIA startup failed")

        def close(self):
            effects.append("closed")

    monkeypatch.setattr("desktop_accessibility.FocusObserver", Observer)
    with (
        pytest.raises(FocusObservationError, match="initialize"),
        focused_control_observer(),
    ):
        pytest.fail("failed observer cannot authorize input")
    assert effects == ["started", "closed"]


def test_query_has_a_short_bound_and_cleanup_kills_a_stuck_provider(monkeypatch):
    effects = []
    observer = FocusObserver()
    commands = io.StringIO()
    output = io.StringIO()
    waits = iter(
        [subprocess.TimeoutExpired("UIA", 0.2), subprocess.TimeoutExpired("UIA", 1), 0]
    )

    def wait(**kwargs):
        result = next(waits)
        if isinstance(result, Exception):
            raise result
        return result

    observer.process = SimpleNamespace(
        stdin=commands,
        stdout=output,
        poll=lambda: None,
        wait=wait,
        terminate=lambda: effects.append("terminate"),
        kill=lambda: effects.append("kill"),
    )

    def timeout(bound):
        assert bound == 1
        raise FocusObservationError("timed out")

    monkeypatch.setattr(observer, "_read", timeout)
    with pytest.raises(FocusObservationError, match="timed out"):
        observer.snapshot()
    observer.close()
    observer.close()
    assert effects == ["terminate", "kill"]
    assert commands.closed and output.closed


def test_native_observer_context_preserves_request_revocation(monkeypatch):
    lifecycle = []

    @contextmanager
    def observer():
        try:
            yield lambda: []
        finally:
            lifecycle.append("closed")

    desktop = DesktopController(mouse=SimpleNamespace())
    monkeypatch.setattr(desktop, "_is_windows_backend", lambda: True)
    monkeypatch.setattr("desktop_accessibility.focused_control_observer", observer)
    with (
        pytest.raises(ScopeViolation, match="stopped"),
        desktop.focused_control_observer(),
    ):
        raise ScopeViolation("request stopped")
    assert lifecycle == ["closed"]


def test_focus_cache_fetches_fresh_properties_together_without_current_reads():
    from desktop_accessibility import _read_cached_focus

    class CachedElement:
        def __init__(self, identity):
            self.values = {
                30000: [42, identity],
                30001: [10, 20, 100, 30],
                30003: 50004,
                30005: "",
                30008: True,
                30019: False,
            }

        def GetCachedPropertyValue(self, property_id):  # noqa: N802 - COM interface
            return self.values[property_id]

    calls = []
    cache = object()

    def fetch(request):
        assert request is cache
        calls.append("fresh")
        return CachedElement(len(calls))

    automation = SimpleNamespace(GetFocusedElementBuildCache=fetch)
    first = _read_cached_focus(automation, cache, {50004: "EditControl"})
    second = _read_cached_focus(automation, cache, {50004: "EditControl"})
    assert calls == ["fresh", "fresh"]
    assert first["identity"] == "42:1" and second["identity"] == "42:2"
    assert first["role"] == "Edit" and first["has_keyboard_focus"] is True
    assert first["bounds"] == [10, 20, 100, 30]
