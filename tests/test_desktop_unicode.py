"""Unicode input through a fake SendInput boundary, never the real desktop."""

import ctypes
import sys
from ctypes import wintypes as w

import pytest

import desktop

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows SendInput ABI")


class Keyboard(ctypes.Structure):
    _fields_ = [
        ("vk", w.WORD),
        ("scan", w.WORD),
        ("flags", w.DWORD),
        ("time", w.DWORD),
        ("extra", ctypes.c_size_t),
    ]


class Mouse(ctypes.Structure):
    _fields_ = [
        ("x", w.LONG),
        ("y", w.LONG),
        ("data", w.DWORD),
        ("flags", w.DWORD),
        ("time", w.DWORD),
        ("extra", ctypes.c_size_t),
    ]


class InputData(ctypes.Union):
    _fields_ = [("keyboard", Keyboard), ("mouse", Mouse)]


class Input(ctypes.Structure):
    _anonymous_ = ("data",)
    _fields_ = [("type", w.DWORD), ("data", InputData)]


class SendInputBoundary:
    argtypes = None
    restype = None

    def __init__(self, effects, responses=()):
        self.effects = effects
        self.responses = iter(responses)

    def __call__(self, count, pointer, size):
        assert size == ctypes.sizeof(Input)
        inputs = ctypes.cast(pointer, ctypes.POINTER(Input))
        events = [
            (
                inputs[i].type,
                inputs[i].keyboard.vk,
                inputs[i].keyboard.scan,
                inputs[i].keyboard.flags,
            )
            for i in range(count)
        ]
        self.effects.append(events)
        return next(self.responses, count)


def test_unicode_input_preserves_utf16_units_and_releases_each_key(monkeypatch):
    effects = []
    boundary = SendInputBoundary(effects)
    monkeypatch.setattr(
        ctypes, "WinDLL", lambda *_a, **_kw: type("Api", (), {"SendInput": boundary})()
    )
    desktop._send_unicode_text_win32("é🙂", lambda: None)
    assert effects == [
        [(1, 0, 0x00E9, 4), (1, 0, 0x00E9, 6)],
        [(1, 0, 0xD83D, 4), (1, 0, 0xD83D, 6), (1, 0, 0xDE42, 4), (1, 0, 0xDE42, 6)],
    ]


def test_unicode_revocation_between_characters_prevents_more_input(monkeypatch):
    effects = []
    boundary = SendInputBoundary(effects)
    monkeypatch.setattr(
        ctypes, "WinDLL", lambda *_a, **_kw: type("Api", (), {"SendInput": boundary})()
    )

    def guard():
        if effects:
            raise PermissionError("request stopped")

    with pytest.raises(PermissionError, match="stopped"):
        desktop._send_unicode_text_win32("ab", guard)
    assert effects == [[(1, 0, 97, 4), (1, 0, 97, 6)]]


def test_partial_unicode_input_releases_held_key_and_reports_uncertainty(monkeypatch):
    effects = []
    boundary = SendInputBoundary(effects, responses=(1, 1))
    monkeypatch.setattr(
        ctypes, "WinDLL", lambda *_a, **_kw: type("Api", (), {"SendInput": boundary})()
    )
    with pytest.raises(
        desktop.DesktopUnavailableError, match=r"partial|incomplete|uncertain"
    ):
        desktop._send_unicode_text_win32("ab", lambda: None)
    assert effects == [[(1, 0, 97, 4), (1, 0, 97, 6)], [(1, 0, 97, 6)]]
