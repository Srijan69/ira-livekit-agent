"""Temporary Windows input marker. Never replaces the user's system cursor.

A native Win32 window runs in a short-lived child process. The layered, transparent, nonactivating window follows the real pointer.
The pipe lifetime owns cleanup, including a parent crash or cancelled operation.
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def input_indicator(points, action):
    if sys.platform != "win32":
        yield None
        return
    process = None
    try:
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), action],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        ready = queue.Queue()
        threading.Thread(
            target=lambda: ready.put(process.stdout.readline()), daemon=True
        ).start()
        try:
            handle = json.loads(ready.get(timeout=3))["hwnd"]
        except (queue.Empty, ValueError, KeyError):
            raise RuntimeError("IRA input indicator could not start.") from None
        yield handle
    finally:
        if process is not None:
            process.stdin.close()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=2)
            process.stdout.close()


def _main(action):
    import ctypes
    from ctypes import wintypes as w

    from PIL import Image, ImageDraw, ImageFont

    from desktop import desktop_coordinate_context

    with desktop_coordinate_context():
        api = ctypes.WinDLL("user32", use_last_error=True)
        gdi = ctypes.WinDLL("gdi32", use_last_error=True)
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
        api.SetWindowPos.argtypes = [
            w.HWND,
            w.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            w.UINT,
        ]
        api.ShowWindow.argtypes = [w.HWND, ctypes.c_int]
        api.DestroyWindow.argtypes = [w.HWND]
        api.SetLayeredWindowAttributes.argtypes = [w.HWND, w.DWORD, w.BYTE, w.DWORD]
        api.SendMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
        api.SendMessageW.restype = ctypes.c_ssize_t
        api.GetCursorPos.argtypes = [ctypes.POINTER(w.POINT)]
        gdi.CreateDIBSection.argtypes = [
            w.HDC,
            ctypes.c_void_p,
            w.UINT,
            ctypes.POINTER(ctypes.c_void_p),
            w.HANDLE,
            w.DWORD,
        ]
        gdi.CreateDIBSection.restype = w.HBITMAP
        gdi.DeleteObject.argtypes = [w.HANDLE]

        class Header(ctypes.Structure):
            _fields_ = [
                ("size", w.DWORD),
                ("width", w.LONG),
                ("height", w.LONG),
                ("planes", w.WORD),
                ("bits", w.WORD),
                ("compression", w.DWORD),
                ("image_size", w.DWORD),
                ("xppm", w.LONG),
                ("yppm", w.LONG),
                ("colors_used", w.DWORD),
                ("colors_important", w.DWORD),
            ]

        canvas = Image.new("RGB", (138, 56), (1, 2, 3))
        draw = ImageDraw.Draw(canvas)
        draw.ellipse((4, 4, 32, 32), outline="#72c9ff", width=3)
        draw.polygon(
            [(18, 18), (18, 36), (23, 31), (28, 40), (32, 38), (27, 29), (35, 28)],
            fill="#72c9ff",
            outline="#103653",
        )
        draw.rounded_rectangle(
            (39, 15, 137, 40), radius=5, fill="#103653", outline="#72c9ff"
        )
        draw.text(
            (46, 21),
            "IRA " + action,
            font=ImageFont.load_default(size=12),
            fill="white",
        )
        header = Header(
            size=ctypes.sizeof(Header), width=138, height=-56, planes=1, bits=32
        )
        pixels = ctypes.c_void_p()
        bitmap = gdi.CreateDIBSection(
            None, ctypes.byref(header), 0, ctypes.byref(pixels), None, 0
        )
        if not bitmap:
            raise ctypes.WinError(ctypes.get_last_error())
        hwnd = None
        try:
            ctypes.memmove(pixels, canvas.tobytes("raw", "BGRX"), 138 * 56 * 4)
            # STATIC + SS_BITMAP has no toolkit activation, focus or pointer logic.
            hwnd = api.CreateWindowExW(
                0x080800A0,
                "STATIC",
                "IRA input indicator",
                0x8000000E,
                0,
                0,
                138,
                56,
                None,
                None,
                None,
                None,
            )
            if not hwnd:
                raise ctypes.WinError(ctypes.get_last_error())
            api.SendMessageW(hwnd, 0x172, 0, bitmap)  # STM_SETIMAGE / IMAGE_BITMAP
            api.SetLayeredWindowAttributes(hwnd, 0x030201, 255, 1)  # transparent color
            stop = threading.Event()

            def watch_parent():
                sys.stdin.read()
                stop.set()

            threading.Thread(target=watch_parent, daemon=True).start()
            expires = time.monotonic() + 15
            message = w.MSG()
            first = True
            while not stop.is_set() and time.monotonic() < expires:
                point = w.POINT()
                api.GetCursorPos(ctypes.byref(point))
                api.SetWindowPos(hwnd, -1, point.x - 18, point.y - 18, 138, 56, 0x50)
                if first:
                    api.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
                    print(json.dumps({"hwnd": hwnd}), flush=True)
                    first = False
                while api.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
                    api.TranslateMessage(ctypes.byref(message))
                    api.DispatchMessageW(ctypes.byref(message))
                stop.wait(0.016)
        finally:
            if hwnd:
                api.DestroyWindow(hwnd)
            gdi.DeleteObject(bitmap)


if __name__ == "__main__":
    _main(sys.argv[1] if len(sys.argv) > 1 else "input")
