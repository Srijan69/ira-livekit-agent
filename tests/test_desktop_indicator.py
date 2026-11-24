"""Native marker geometry and helper arguments, without interacting with apps."""

import io
import json
import sys
from types import SimpleNamespace

import pytest

import desktop_indicator


def test_target_marker_is_centered_on_supplied_native_point():
    image, bounds = desktop_indicator.target_marker(((500, -100),), "click")
    assert bounds == (476, -124, 49, 49)
    assert image.size == (49, 49)
    assert image.getpixel((24, 24)) != (1, 2, 3)
    assert image.getpixel((24, 6)) != (1, 2, 3)
    assert image.getpixel((0, 0)) == (1, 2, 3)


def test_drag_marker_contains_both_endpoints_and_connecting_path():
    image, bounds = desktop_indicator.target_marker(((100, 100), (300, 200)), "drag")
    assert bounds == (76, 76, 249, 149)
    assert image.getpixel((24, 24)) != (1, 2, 3)
    assert image.getpixel((224, 124)) != (1, 2, 3)
    assert image.getpixel((124, 74)) != (1, 2, 3)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows helper process flags")
def test_indicator_helper_receives_target_coordinates_and_cleans_up(monkeypatch):
    calls = []
    process = SimpleNamespace(
        stdin=io.StringIO(),
        stdout=io.StringIO('{"hwnd":17}\n'),
        wait=lambda timeout: calls.append(("wait", timeout)),
    )

    def start(command, **kwargs):
        calls.append(command)
        return process

    monkeypatch.setattr(desktop_indicator.subprocess, "Popen", start)
    with desktop_indicator.input_indicator(((500, -100), (700, 20)), "drag") as handle:
        assert handle == 17
        command = calls[0]
        assert command[-2] == "drag"
        assert json.loads(command[-1]) == [[500, -100], [700, 20]]
    assert process.stdin.closed
    assert process.stdout.closed


@pytest.mark.skipif(sys.platform != "win32", reason="Windows helper process flags")
def test_fast_action_marker_stays_visible_for_a_perceptible_cue(monkeypatch):
    process = SimpleNamespace(
        stdin=io.StringIO(),
        stdout=io.StringIO('{"hwnd":17}\n'),
        wait=lambda timeout: None,
    )
    monkeypatch.setattr(
        desktop_indicator.subprocess, "Popen", lambda *_a, **_kw: process
    )
    clock = [0.0]
    pauses = []
    monkeypatch.setattr(desktop_indicator.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        desktop_indicator.time, "sleep", lambda seconds: pauses.append(seconds)
    )
    with desktop_indicator.input_indicator(((500, 100),), "click"):
        clock[0] = 0.01
    assert pauses and 0.1 <= pauses[0] <= 0.2
