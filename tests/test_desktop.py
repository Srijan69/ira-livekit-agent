from pathlib import Path

import pytest

from desktop import DesktopController, DesktopUnavailableError


class FakeScreenshot:
    size = (800, 600)

    def save(self, path: str, **kwargs) -> None:
        Path(path).write_bytes(b"png-data")

    def convert(self, mode: str):
        assert mode == "RGB"
        return self

    def tobytes(self) -> bytes:
        return b"rgb-data"


class FakeMouse:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def size(self) -> tuple[int, int]:
        return (800, 600)

    def screenshot(self) -> FakeScreenshot:
        self.calls.append(("screenshot",))
        return FakeScreenshot()

    def moveTo(self, x: int, y: int, duration: float = 0.0) -> None:  # noqa: N802
        self.calls.append(("moveTo", x, y, duration))

    def click(self, x: int, y: int, button: str = "left") -> None:
        self.calls.append(("click", x, y, button))

    def write(self, text: str, interval: float = 0.0) -> None:
        self.calls.append(("write", text, interval))

    def scroll(self, clicks: int) -> None:
        self.calls.append(("scroll", clicks))


def test_desktop_actions_validate_coordinates_and_delegate(tmp_path) -> None:
    mouse = FakeMouse()
    desktop = DesktopController(mouse=mouse)

    desktop.move(10, 20)
    desktop.click(30, 40)
    desktop.scroll(-2)
    desktop.type_text("hello")

    assert mouse.calls == [
        ("moveTo", 10, 20, 0.0),
        ("click", 30, 40, "left"),
        ("scroll", -2),
        ("write", "hello", 0.0),
    ]


def test_desktop_rejects_out_of_bounds_coordinates() -> None:
    desktop = DesktopController(mouse=FakeMouse())

    with pytest.raises(ValueError, match="outside"):
        desktop.click(800, 10)


def test_capture_screen_writes_png(tmp_path) -> None:
    desktop = DesktopController(mouse=FakeMouse(), capture_dir=tmp_path)

    capture = desktop.capture_screen()

    assert capture.path.parent == tmp_path
    assert capture.path.read_bytes() == b"png-data"
    assert capture.width == 800
    assert capture.height == 600
    assert capture.rgb_bytes == b"rgb-data"


def test_missing_desktop_backend_is_explicit() -> None:
    desktop = DesktopController(mouse=None, loader=lambda: None)

    with pytest.raises(DesktopUnavailableError):
        desktop.screen_size()
