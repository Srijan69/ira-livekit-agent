"""Request revocation at the final physical-input boundary."""

from types import SimpleNamespace

import pytest

from desktop import DesktopController, DesktopUnavailableError


@pytest.fixture
def guarded_desktop():
    events = []
    held = set()
    state = SimpleNamespace(revoked=False)
    window = SimpleNamespace(
        title="Owned fixture", left=0, top=0, width=800, height=600
    )

    def guard():
        if state.revoked:
            raise PermissionError("request revoked")

    def down(*, button):
        held.add(button)
        events.append(("down", button))

    def up(*, button):
        held.remove(button)
        events.append(("up", button))

    backend = SimpleNamespace(
        size=lambda: (800, 600),
        getActiveWindow=lambda: window,
        moveTo=lambda x, y, **kwargs: events.append(("move", x, y)),
        click=lambda *args, **kwargs: events.append(("click",)),
        scroll=lambda amount: events.append(("scroll", amount)),
        hscroll=lambda amount: events.append(("hscroll", amount)),
        mouseDown=down,
        mouseUp=up,
        write=lambda *args, **kwargs: events.append(("write",)),
        press=lambda *args, **kwargs: events.append(("press",)),
    )
    controller = DesktopController(mouse=backend)
    controller.input_guard = guard
    return controller, backend, state, events, held


@pytest.mark.parametrize(
    "operation",
    [
        lambda desktop: desktop.move(20, 30),
        lambda desktop: desktop.click(20, 30),
        lambda desktop: desktop.scroll(2),
        lambda desktop: desktop.scroll(2, horizontal=True),
        lambda desktop: desktop.drag(20, 30, 40, 50),
        lambda desktop: desktop.type_text("owned fixture"),
        lambda desktop: desktop.press("enter"),
        lambda desktop: desktop.type_in_window("owned fixture", "Owned fixture"),
        lambda desktop: desktop.press_in_window("enter", "Owned fixture"),
    ],
    ids=[
        "move",
        "click",
        "wheel",
        "hwheel",
        "drag",
        "type",
        "press",
        "window-type",
        "window-press",
    ],
)
def test_revoked_guard_rejects_input_before_any_native_effect(
    guarded_desktop, operation
):
    controller, _, state, events, held = guarded_desktop
    state.revoked = True
    with pytest.raises((PermissionError, DesktopUnavailableError)):
        operation(controller)
    assert events == []
    assert held == set()


def test_drag_revocation_after_positioning_prevents_mouse_down(guarded_desktop):
    controller, backend, state, events, held = guarded_desktop

    def revoke(x, y, **kwargs):
        events.append(("move", x, y))
        state.revoked = True

    backend.moveTo = revoke
    with pytest.raises(PermissionError, match="revoked"):
        controller.drag(20, 30, 40, 50)
    assert events == [("move", 20, 30)]
    assert held == set()


def test_drag_revocation_with_button_down_still_releases_without_destination_move(
    guarded_desktop,
):
    controller, backend, state, events, held = guarded_desktop
    down = backend.mouseDown

    def revoke(**kwargs):
        down(**kwargs)
        state.revoked = True

    backend.mouseDown = revoke
    with pytest.raises(PermissionError, match="revoked"):
        controller.drag(20, 30, 40, 50)
    assert events == [("move", 20, 30), ("down", "left"), ("up", "left")]
    assert held == set()
