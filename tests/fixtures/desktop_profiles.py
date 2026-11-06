"""Owned native Windows fixture with accessible profile buttons. No personal apps."""

import ctypes
import json
import sys
from ctypes import wintypes as w
from pathlib import Path

api = ctypes.WinDLL("user32", use_last_error=True)
kernel = ctypes.WinDLL("kernel32", use_last_error=True)
api.SetProcessDPIAware()
kernel.GetModuleHandleW.restype = w.HMODULE
instance = kernel.GetModuleHandleW(None)
PROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
api.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
api.DefWindowProcW.restype = ctypes.c_ssize_t
api.CreateWindowExW.argtypes = [
    w.DWORD,
    w.LPCWSTR,
    w.LPCWSTR,
    w.DWORD,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    w.HWND,
    w.HMENU,
    w.HINSTANCE,
    ctypes.c_void_p,
]
api.CreateWindowExW.restype = w.HWND
api.SetWindowTextW.argtypes = [w.HWND, w.LPCWSTR]
api.ShowWindow.argtypes = [w.HWND, ctypes.c_int]
api.SetForegroundWindow.argtypes = [w.HWND]
api.IsDialogMessageW.argtypes = [w.HWND, ctypes.POINTER(w.MSG)]
api.PostQuitMessage.argtypes = [ctypes.c_int]
ready = Path(sys.argv[1])
title = sys.argv[2]


@PROC
def proc(hwnd, message, wp, lp):
    if message == 0x111:
        command = wp & 0xFFFF
        if command == 9:
            ready.write_text(json.dumps({"hwnd": hwnd}))
        elif command in (1, 2, 3):
            selected = {1: "Guest", 2: "kommidi", 3: "kommidi work"}[command]
            ready.with_suffix(".result").write_text(selected)
            api.SetWindowTextW(hwnd, title + " — Selected " + selected)
        return 0
    if message == 2:
        api.PostQuitMessage(0)
        return 0
    return api.DefWindowProcW(hwnd, message, wp, lp)


class WNDCLASS(ctypes.Structure):
    _fields_ = [
        ("style", w.UINT),
        ("lpfnWndProc", PROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", w.HINSTANCE),
        ("hIcon", w.HICON),
        ("hCursor", w.HANDLE),
        ("hbrBackground", w.HBRUSH),
        ("lpszMenuName", w.LPCWSTR),
        ("lpszClassName", w.LPCWSTR),
    ]


wc = WNDCLASS(
    lpfnWndProc=proc,
    hInstance=instance,
    hbrBackground=6,
    lpszClassName="IRAProfileFixture",
)
api.RegisterClassW(ctypes.byref(wc))
hwnd = api.CreateWindowExW(
    0,
    wc.lpszClassName,
    title,
    0x00CF0000,
    120,
    100,
    900,
    600,
    None,
    None,
    instance,
    None,
)
if not hwnd:
    raise ctypes.WinError(ctypes.get_last_error())
for label, command, x, y in [
    ("Start test", 9, 50, 50),
    ("Guest", 1, 50, 160),
    ("kommidi", 2, 450, 160),
    ("kommidi work", 3, 450, 350),
]:
    api.CreateWindowExW(
        0, "BUTTON", label, 0x50010000, x, y, 270, 120, hwnd, command, instance, None
    )
api.ShowWindow(hwnd, 5)
api.SetForegroundWindow(hwnd)
message = w.MSG()
while api.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
    if api.IsDialogMessageW(hwnd, ctypes.byref(message)):
        continue
    api.TranslateMessage(ctypes.byref(message))
    api.DispatchMessageW(ctypes.byref(message))
