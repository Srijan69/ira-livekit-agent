"""Bounded, read-only Windows accessibility snapshots in an isolated process."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def read_elements(hwnd: int) -> list[dict]:
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), str(hwnd)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=5,
        check=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return json.loads(result.stdout)


def _main(hwnd):
    from collections import deque

    import uiautomation as auto

    from desktop import desktop_coordinate_context

    with desktop_coordinate_context():
        root = auto.ControlFromHandle(hwnd)
        pending = deque([(root, 0)]) if root else deque()
        result = []
        visited = 0
        roles = {
            "ButtonControl",
            "HyperlinkControl",
            "ListItemControl",
            "MenuItemControl",
            "EditControl",
            "TabItemControl",
            "CheckBoxControl",
            "RadioButtonControl",
            "ComboBoxControl",
            "TreeItemControl",
            "TextControl",
        }
        while pending and visited < 600 and len(result) < 160:
            control, depth = pending.popleft()
            visited += 1
            try:
                if control.IsPassword:
                    continue
                if depth < 16:
                    pending.extend(
                        (child, depth + 1) for child in control.GetChildren()
                    )
                if (
                    control.ControlTypeName not in roles
                    or control.IsOffscreen
                    or not control.IsEnabled
                ):
                    continue
                name = control.Name.strip()
                rect = control.BoundingRectangle
                if not name or rect.width() <= 0 or rect.height() <= 0:
                    continue
                result.append(
                    {
                        "identity": ":".join(map(str, control.GetRuntimeId())),
                        "name": name[:240],
                        "role": control.ControlTypeName.removesuffix("Control"),
                        "bounds": [rect.left, rect.top, rect.width(), rect.height()],
                    }
                )
            except Exception:
                # Providers may destroy individual controls during enumeration.
                continue
        sys.stdout.reconfigure(encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    _main(int(sys.argv[1]))
