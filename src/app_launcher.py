"""Launch a registered Windows app without a Start-menu/web-search fallback."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from request_scope_livekit import check_current_action


@dataclass(frozen=True)
class InstalledApp:
    name: str
    app_id: str


_ALIASES = {
    "calc": "calculator",
    "chrome": "google chrome",
    "edge": "microsoft edge",
    "vscode": "visual studio code",
    "vs code": "visual studio code",
    "cmd": "command prompt",
    "ms paint": "paint",
}


def select_installed_app(app_name: str, apps: list[InstalledApp]) -> InstalledApp:
    """Match exact display names, with a small fixed alias table; never guess."""
    if not isinstance(app_name, str) or not app_name.strip():
        raise ValueError("A nonempty application name is required")
    name = " ".join(app_name.split()).casefold()
    wanted = _ALIASES.get(name, name)
    matches = {}
    for app in apps:
        if " ".join(app.name.split()).casefold() == wanted:
            matches[app.app_id] = app
    if not matches:
        raise ValueError(f"{app_name} is not installed in the registered app catalogue")
    if len(matches) != 1:
        raise ValueError(f"Application name {app_name} is ambiguous; specify which app")
    return next(iter(matches.values()))


def _read_start_apps() -> list[InstalledApp]:
    if sys.platform != "win32":
        raise RuntimeError("Windows app catalogue is unavailable on this host")
    executable = (
        Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
        / "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    # The script is constant; no app names or model arguments enter shell code.
    script = (
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); "
        "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress"
    )
    try:
        result = subprocess.run(
            [str(executable), "-NoProfile", "-NonInteractive", "-Command", script],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8-sig",
            timeout=8,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if len(result.stdout) > 2_000_000:
            raise ValueError("App catalogue exceeds the supported size")
        rows = json.loads(result.stdout)
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list) or any(
            not isinstance(row, dict)
            or not isinstance(row.get("Name"), str)
            or not isinstance(row.get("AppID"), str)
            for row in rows
        ):
            raise ValueError("Invalid app catalogue")
        return [InstalledApp(row["Name"], row["AppID"]) for row in rows]
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise RuntimeError("Windows app catalogue is unavailable or invalid") from exc


def _start_app(app: InstalledApp) -> None:
    if sys.platform != "win32":
        raise RuntimeError("Windows app launching is unavailable on this host")
    if not re.fullmatch(r"[A-Za-z0-9_.{}!\-]{1,256}", app.app_id):
        raise ValueError("The registered application ID cannot be launched safely")
    check_current_action()
    os.startfile("shell:AppsFolder\\" + app.app_id)


def launch_installed_app(app_name: str) -> InstalledApp:
    """Issue one launch for an exact registered target; this is not verification."""
    check_current_action()
    app = select_installed_app(app_name, _read_start_apps())
    check_current_action()
    _start_app(app)
    return app
