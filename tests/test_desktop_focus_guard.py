"""Shared native child windows must not hide focused-control changes."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from PIL import Image

from computer_use import ObservedDesktop
from desktop import DesktopController, DesktopUnavailableError


@pytest.fixture
def editor(tmp_path):
    focus = ["first"]
    writes = []
    window = SimpleNamespace(title="Owned editor", left=0, top=0, width=800, height=600)
    controls = [
        {
            "identity": "first",
            "name": "First field",
            "role": "Edit",
            "bounds": [100, 100, 200, 40],
        },
        {
            "identity": "second",
            "name": "Second field",
            "role": "Edit",
            "bounds": [100, 200, 200, 40],
        },
    ]

    def elements(_window):
        return [
            {**control, "has_keyboard_focus": control["identity"] == focus[0]}
            for control in controls
        ]

    backend = SimpleNamespace(
        size=lambda: (800, 600),
        screenshot=lambda: Image.new("RGB", (800, 600), "white"),
        getActiveWindow=lambda: window,
        getAllWindows=lambda: [window],
        get_keyboard_focus=lambda _window: 42,
        get_accessible_elements=elements,
        write=lambda text, **kwargs: writes.append((focus[0], text)),
        press=lambda key: writes.append((focus[0], key)),
    )
    desktop = DesktopController(mouse=backend, capture_dir=tmp_path)
    computer = ObservedDesktop(desktop)
    yield desktop, computer, backend, focus, controls, writes
    computer.close()


def test_observed_typing_stops_before_next_character_when_uia_focus_changes(editor):
    _, computer, backend, focus, _, writes = editor
    frame = computer.observe()

    def write(text, **kwargs):
        writes.append((focus[0], text))
        focus[0] = "second"

    backend.write = write
    with pytest.raises(DesktopUnavailableError, match="partial"):
        computer.act(frame.id, "type", text="abc")
    assert writes == [("first", "a")]
    assert computer._frame is None


def test_direct_typing_tracks_unnamed_focused_controls(editor):
    desktop, _, backend, focus, controls, writes = editor
    for control in controls:
        control["name"] = ""

    def write(text, **kwargs):
        writes.append((focus[0], text))
        focus[0] = "second"

    backend.write = write
    with pytest.raises(DesktopUnavailableError, match="partial"):
        desktop.type_in_window("abc", "Owned editor")
    assert writes == [("first", "a")]


def test_observed_key_rejects_same_control_semantic_drift_during_preview(editor):
    _, computer, _, _, controls, writes = editor
    frame = computer.observe()
    with pytest.raises(ValueError, match="focused control"):
        computer.act(
            frame.id,
            "key",
            key="enter",
            _on_action=lambda *_: controls[0].update(name="Different field"),
        )
    assert writes == []


def test_focus_observer_is_closed_if_typing_is_revoked(editor):
    desktop, _, _, _, _, writes = editor
    lifecycle = []

    @contextmanager
    def observer():
        lifecycle.append("started")
        try:
            yield lambda: []
        finally:
            lifecycle.append("closed")

    desktop.focused_control_observer = observer

    def stop():
        raise PermissionError("request stopped")

    with pytest.raises(PermissionError, match="stopped"):
        desktop.type_in_window("abc", "Owned editor", _guard=stop)
    assert lifecycle == ["started", "closed"]
    assert writes == []


def test_focus_observer_failure_prevents_text_input(editor):
    desktop, _, _, _, _, writes = editor

    @contextmanager
    def unavailable():
        raise DesktopUnavailableError("Focused control unavailable")
        yield

    desktop.focused_control_observer = unavailable
    with pytest.raises(DesktopUnavailableError, match="unavailable"):
        desktop.type_in_window("abc", "Owned editor")
    assert writes == []


def test_focus_query_failure_mid_text_reports_partial_input_and_closes(editor):
    desktop, _, _, _, _, writes = editor
    lifecycle = []

    @contextmanager
    def observer():
        def snapshot():
            if writes:
                raise DesktopUnavailableError("Focused control unavailable")
            return []

        try:
            yield snapshot
        finally:
            lifecycle.append("closed")

    desktop.focused_control_observer = observer
    with pytest.raises(DesktopUnavailableError, match="partial"):
        desktop.type_in_window("abc", "Owned editor")
    assert writes == [("first", "a")]
    assert lifecycle == ["closed"]


def test_focus_response_cannot_authorize_input_after_foreground_changes(editor):
    desktop, _, backend, _, controls, writes = editor
    reads = 0
    stale_focus = [{**controls[0], "has_keyboard_focus": True}]

    @contextmanager
    def observer():
        def snapshot():
            nonlocal reads
            reads += 1
            if reads == 3:  # Focus changes while the first character's IPC read runs.
                backend.getActiveWindow = lambda: SimpleNamespace(
                    title="Another application", left=0, top=0, width=800, height=600
                )
            return stale_focus

        yield snapshot

    desktop.focused_control_observer = observer
    with pytest.raises(DesktopUnavailableError, match="partial"):
        desktop.type_in_window("ab", "Owned editor")
    assert writes == []


def test_focus_response_cannot_authorize_input_after_request_revocation(editor):
    desktop, _, _, _, controls, writes = editor
    reads = 0
    allowed = True
    stale_focus = [{**controls[0], "has_keyboard_focus": True}]

    @contextmanager
    def observer():
        def snapshot():
            nonlocal reads, allowed
            reads += 1
            if reads == 3:
                allowed = False
            return stale_focus

        yield snapshot

    def permit():
        if not allowed:
            raise PermissionError("request revoked during focus observation")

    desktop.focused_control_observer = observer
    desktop.input_guard = permit
    with pytest.raises(DesktopUnavailableError, match="partial"):
        desktop.type_in_window("ab", "Owned editor", _guard=permit)
    assert writes == []


def test_typing_continues_when_same_focused_control_grows_with_its_text(editor):
    desktop, _, backend, focus, controls, writes = editor

    def write(text, **kwargs):
        writes.append((focus[0], text))
        controls[0]["bounds"][3] += 10

    backend.write = write
    desktop.type_in_window("abc", "Owned editor")
    assert writes == [("first", "a"), ("first", "b"), ("first", "c")]
