"""Bounded system metric sampling, called off the voice event loop."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


class SystemStatusUnavailableError(RuntimeError):
    """The host cannot provide system metrics."""


class SystemStatusReader:
    def __init__(
        self,
        *,
        loader: Callable[[], Any] | None = None,
        disk_path: Path | str | None = None,
    ) -> None:
        self._disk_path = str(disk_path or Path.home().anchor)
        try:
            self._backend = (loader or self._load_psutil)()
            self._backend.cpu_percent(interval=None)
        except Exception as exc:
            raise SystemStatusUnavailableError("System metrics unavailable") from exc

    @staticmethod
    def _load_psutil() -> Any:
        import psutil

        return psutil

    def read(self) -> dict[str, float | bool | None]:
        try:
            # psutil's nonblocking CPU baseline is thread-local. A fresh short
            # sample stays accurate when asyncio.to_thread picks a new worker.
            cpu = self._backend.cpu_percent(interval=0.1)
            memory = self._backend.virtual_memory()
            disk = self._backend.disk_usage(self._disk_path)
            battery = self._backend.sensors_battery()
            return {
                "cpu_percent": float(cpu),
                "memory_percent": float(memory.percent),
                "disk_percent": float(disk.percent),
                "battery_percent": float(battery.percent) if battery else None,
                "power_plugged": bool(battery.power_plugged) if battery else None,
            }
        except Exception as exc:
            raise SystemStatusUnavailableError("Could not read system metrics") from exc
