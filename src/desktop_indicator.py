"""Temporary Windows input marker. Never replaces the user's system cursor.

A native Win32 window runs in a short-lived child process. Layered,
transparent, nonactivating windows mark the observed target and real pointer.
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
    shown_at = None
    try:
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), action, json.dumps(points)],
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
        shown_at = time.monotonic()
        yield handle
    finally:
        if process is not None:
            if shown_at is not None:
                remaining = 0.12 - (time.monotonic() - shown_at)
                if remaining > 0:
                    time.sleep(remaining)
            process.stdin.close()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=2)
            process.stdout.close()


def target_marker(points, action):
    """Build a fixed native-coordinate target ring and optional drag path."""
    from PIL import Image, ImageDraw

    if (
        not points
        or len(points) > 2
        or any(
            len(point) != 2 or any(type(value) is not int for value in point)
            for point in points
        )
    ):
        raise ValueError("Indicator targets require one or two integer points.")
    left = min(point[0] for point in points) - 24
    top = min(point[1] for point in points) - 24
    width = max(point[0] for point in points) - left + 25
    height = max(point[1] for point in points) - top + 25
    if width * height > 16_000_000:
        raise ValueError("Indicator path requires separate bounded segments.")
    canvas = Image.new("RGB", (width, height), (1, 2, 3))
    draw = ImageDraw.Draw(canvas)
    centers = [(x - left, y - top) for x, y in points]
    if action == "drag" and len(centers) == 2:
        draw.line(centers, fill="#5e8ca8", width=2)
    for x, y in centers:
        draw.ellipse((x - 18, y - 18, x + 18, y + 18), outline="#78baef", width=2)
        draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill="#78baef")
        if action == "click":
            draw.ellipse((x - 12, y - 12, x + 12, y + 12), outline="#46718f", width=1)
    return canvas, (left, top, width, height)


def _target_layers(points, action):
    if not points:
        return []
    try:
        return [target_marker(points, action)]
    except ValueError:
        # A diagonal across a large monitor wall must not allocate its full area.
        layers = [target_marker((point,), action) for point in points]
        if action == "drag" and len(points) == 2:
            start, end = points
            steps = max(abs(end[0] - start[0]), abs(end[1] - start[1])) // 500 + 1
            for step in range(steps):
                pair = tuple(
                    (
                        start[0] + (end[0] - start[0]) * n // steps,
                        start[1] + (end[1] - start[1]) * n // steps,
                    )
                    for n in (step, step + 1)
                )
                layers.append(target_marker(pair, "drag"))
        return layers


def _main(action, points=()):
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

        canvas = Image.new("RGB", (112, 28), (1, 2, 3))
        draw = ImageDraw.Draw(canvas)
        draw.rounded_rectangle(
            (0, 0, 111, 27), radius=7, fill="#183345", outline="#5e8ca8"
        )
        draw.text(
            (9, 7),
            "IRA " + action,
            font=ImageFont.load_default(size=12),
            fill="#d4e9f7",
        )
        windows = []
        bitmaps = []

        def create_marker(image, bounds):
            left, top, width, height = bounds
            header = Header(
                size=ctypes.sizeof(Header),
                width=width,
                height=-height,
                planes=1,
                bits=32,
            )
            pixels = ctypes.c_void_p()
            bitmap = gdi.CreateDIBSection(
                None, ctypes.byref(header), 0, ctypes.byref(pixels), None, 0
            )
            if not bitmap:
                raise ctypes.WinError(ctypes.get_last_error())
            bitmaps.append(bitmap)
            ctypes.memmove(pixels, image.tobytes("raw", "BGRX"), width * height * 4)
            hwnd = api.CreateWindowExW(
                0x080800A0,
                "STATIC",
                "IRA input indicator",
                0x8000000E,
                left,
                top,
                width,
                height,
                None,
                None,
                None,
                None,
            )
            if not hwnd:
                raise ctypes.WinError(ctypes.get_last_error())
            windows.append(hwnd)
            api.SendMessageW(hwnd, 0x172, 0, bitmap)  # STM_SETIMAGE / IMAGE_BITMAP
            api.SetLayeredWindowAttributes(hwnd, 0x030201, 255, 1)
            api.SetWindowPos(hwnd, -1, left, top, width, height, 0x50)
            api.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
            return hwnd

        try:
            target_windows = [
                create_marker(image, bounds)
                for image, bounds in _target_layers(points, action)
            ]
            point = w.POINT()
            api.GetCursorPos(ctypes.byref(point))
            hwnd = create_marker(canvas, (point.x + 14, point.y + 14, 112, 28))
            stop = threading.Event()

            def watch_parent():
                sys.stdin.read()
                stop.set()

            threading.Thread(target=watch_parent, daemon=True).start()
            expires = time.monotonic() + 120
            message = w.MSG()
            print(
                json.dumps({"hwnd": target_windows[0] if target_windows else hwnd}),
                flush=True,
            )
            while not stop.is_set() and time.monotonic() < expires:
                point = w.POINT()
                api.GetCursorPos(ctypes.byref(point))
                api.SetWindowPos(hwnd, -1, point.x + 14, point.y + 14, 112, 28, 0x50)
                while api.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
                    api.TranslateMessage(ctypes.byref(message))
                    api.DispatchMessageW(ctypes.byref(message))
                stop.wait(0.016)
        finally:
            for hwnd in reversed(windows):
                api.DestroyWindow(hwnd)
            for bitmap in bitmaps:
                gdi.DeleteObject(bitmap)


if __name__ == "__main__":
    _main(
        sys.argv[1] if len(sys.argv) > 1 else "input",
        json.loads(sys.argv[2]) if len(sys.argv) > 2 else (),
    )
