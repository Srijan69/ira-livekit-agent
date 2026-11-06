"""Immutable physical monitor geometry in Windows virtual-desktop pixels."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Monitor:
    id: str
    bounds: tuple[int, int, int, int]
    primary: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("A monitor requires a stable nonempty identity.")
        if (
            type(self.bounds) is not tuple
            or len(self.bounds) != 4
            or any(type(value) is not int for value in self.bounds)
            or type(self.primary) is not bool
        ):
            raise ValueError("Monitor bounds must be four physical-pixel integers.")
        x, y, width, height = self.bounds
        if (
            width <= 0
            or height <= 0
            or not -(2**31) <= x <= x + width - 1 < 2**31
            or not -(2**31) <= y <= y + height - 1 < 2**31
        ):
            raise ValueError(
                "Monitor bounds must describe a positive native rectangle."
            )


@dataclass(frozen=True)
class DesktopLayout:
    monitors: tuple[Monitor, ...]

    def __post_init__(self) -> None:
        if (
            type(self.monitors) is not tuple
            or not self.monitors
            or any(not isinstance(monitor, Monitor) for monitor in self.monitors)
        ):
            raise ValueError("Desktop layout requires a nonempty tuple of monitors.")
        if len({monitor.id for monitor in self.monitors}) != len(self.monitors):
            raise ValueError("Monitor identities must be unique.")
        if sum(monitor.primary for monitor in self.monitors) > 1:
            raise ValueError("Desktop layout has more than one primary monitor.")
        # Native enumeration order is not part of a topology's identity.
        object.__setattr__(
            self,
            "monitors",
            tuple(sorted(self.monitors, key=lambda monitor: monitor.id)),
        )

    @property
    def bounds(self) -> tuple[int, int, int, int]:
        left = min(monitor.bounds[0] for monitor in self.monitors)
        top = min(monitor.bounds[1] for monitor in self.monitors)
        right = max(monitor.bounds[0] + monitor.bounds[2] for monitor in self.monitors)
        bottom = max(monitor.bounds[1] + monitor.bounds[3] for monitor in self.monitors)
        return left, top, right - left, bottom - top

    def contains(self, x: int, y: int) -> bool:
        """Reject gaps inside the bounding box and nonphysical coordinates."""
        if type(x) is not int or type(y) is not int:
            return False
        return any(
            left <= x < left + width and top <= y < top + height
            for left, top, width, height in (
                monitor.bounds for monitor in self.monitors
            )
        )
