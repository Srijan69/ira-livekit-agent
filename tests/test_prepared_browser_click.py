"""Prepared native clicks require fresh, provable target ownership at input."""

from types import SimpleNamespace

import pytest

from tests.fixtures.playwright_fakes import FakeElement, FakePage, controller_with


class PreparedElement(FakeElement):
    def __init__(self):
        super().__init__("button", aria_label="Continue")
        self.frame = None
        self.main_document = True
        self.hit = True
        self.box = {"x": 200.0, "y": 120.0, "width": 100.0, "height": 40.0}
        self.geometry_reads = 0
        self.on_geometry = None
        self.on_hit = None
        self.actual_handle_clicks = 0

    def owner_frame(self):
        return self.frame

    def bounding_box(self):
        self.geometry_reads += 1
        if self.on_geometry:
            self.on_geometry(self.geometry_reads)
        return None if self.box is None else dict(self.box)

    def evaluate(self, expression, arg=None):
        if "elementFromPoint" in expression:
            if self.on_hit:
                self.on_hit()
            if self.box != arg[1]:
                return False
            if self.stale or not self.main_document or not self.is_enabled():
                return False
            if len(arg) > 2:
                metadata = super().evaluate("metadata")
                observed = [metadata["role"], " ".join(metadata["label"].split())[:80]]
                if list(arg[2]) != observed:
                    return False
                if len(arg) > 3 and list(arg[3]) != [metadata["tag"], metadata["type"]]:
                    return False
            return self.hit
        if "getRootNode" in expression:
            return self.main_document
        return super().evaluate(expression, arg)

    def click(self, *, trial=False, **kwargs):
        if not trial:
            self.actual_handle_clicks += 1
        return super().click(trial=trial, **kwargs)


def fixture():
    element = PreparedElement()
    page = FakePage([element])
    element.frame = page
    page.viewport_size = {"width": 800, "height": 600}
    mouse_calls = []
    page.mouse = SimpleNamespace(
        click=lambda x, y, **kwargs: mouse_calls.append((x, y))
    )
    page.evaluator = lambda expression, arg: {"width": 800, "height": 600}
    return controller_with(page), page, element, mouse_calls


def test_main_document_prepared_click_uses_one_native_mouse_dispatch():
    controller, _, element, mouse_calls = fixture()
    try:
        controller.list_elements()
        controller.click(1)
        assert mouse_calls == [(250.0, 140.0)]
        assert element.actual_handle_clicks == 0
    finally:
        controller.close()


@pytest.mark.parametrize("kind", ["iframe", "shadow"])
def test_unproven_frame_or_shadow_keeps_standard_native_element_path(kind):
    controller, _, element, mouse_calls = fixture()
    if kind == "iframe":
        element.frame = SimpleNamespace()
    else:
        element.main_document = False
    try:
        controller.list_elements()
        controller.click(1)
        assert mouse_calls == []
        assert element.actual_handle_clicks == 1
    finally:
        controller.close()


def assert_no_input(controller, element, mouse_calls, action):
    try:
        controller.list_elements()
        with pytest.raises(RuntimeError):
            action()
        assert mouse_calls == []
        assert element.actual_handle_clicks == 0
    finally:
        controller.close()


def test_overlay_appearing_after_trial_refuses_prepared_click():
    controller, _, element, mouse_calls = fixture()
    element.on_trial = lambda: setattr(element, "hit", False)
    assert_no_input(controller, element, mouse_calls, lambda: controller.click(1))


@pytest.mark.parametrize("proof", [None, "true", 1])
def test_unavailable_or_nonboolean_hit_proof_refuses_click(proof):
    controller, _, element, mouse_calls = fixture()
    element.hit = proof
    assert_no_input(controller, element, mouse_calls, lambda: controller.click(1))


def test_bounds_moving_between_geometry_reads_refuse_click():
    controller, _, element, mouse_calls = fixture()

    def move(read):
        if read > 1:
            element.box["x"] = 240.0

    element.on_geometry = move
    assert_no_input(controller, element, mouse_calls, lambda: controller.click(1))


def test_role_changes_during_geometry_reads_refuse_click():
    controller, _, element, mouse_calls = fixture()
    element.on_geometry = lambda _: element._attrs.update(role="checkbox")
    assert_no_input(controller, element, mouse_calls, lambda: controller.click(1))


def test_disabled_target_after_trial_refuses_click():
    controller, _, element, mouse_calls = fixture()
    element.on_trial = lambda: setattr(element, "_enabled", False)
    assert_no_input(controller, element, mouse_calls, lambda: controller.click(1))


def test_target_disabled_during_final_point_proof_refuses_click():
    controller, _, element, mouse_calls = fixture()
    reads = [0]

    def disable_on_final_proof():
        reads[0] += 1
        if reads[0] == 2:
            element._enabled = False

    element.on_hit = disable_on_final_proof
    assert_no_input(controller, element, mouse_calls, lambda: controller.click(1))
    assert reads[0] == 2


def test_missing_owner_frame_proof_refuses_click():
    controller, _, element, mouse_calls = fixture()
    element.frame = None
    assert_no_input(controller, element, mouse_calls, lambda: controller.click(1))


def test_missing_document_root_proof_refuses_click():
    controller, _, element, mouse_calls = fixture()
    element.main_document = None
    assert_no_input(controller, element, mouse_calls, lambda: controller.click(1))


def test_selector_url_changes_during_geometry_reads_refuse_click():
    controller, page, element, mouse_calls = fixture()
    expected = page.url
    element.on_geometry = lambda _: setattr(page, "url", expected + "?changed")
    assert_no_input(
        controller,
        element,
        mouse_calls,
        lambda: controller.click_selector("button", expected),
    )


def test_layout_changes_during_final_preflight_refuse_prepared_point():
    controller, _, element, mouse_calls = fixture()
    reads = [0]

    def preflight():
        reads[0] += 1
        if reads[0] == 2:
            element.box["x"] = 400.0

    assert_no_input(
        controller,
        element,
        mouse_calls,
        lambda: controller._worker.run(
            controller._native_click, element, preflight=preflight
        ),
    )


def test_overlay_appearing_during_final_preflight_refuses_prepared_point():
    controller, _, element, mouse_calls = fixture()
    reads = [0]

    def preflight():
        reads[0] += 1
        if reads[0] == 2:
            element.hit = False

    assert_no_input(
        controller,
        element,
        mouse_calls,
        lambda: controller._worker.run(
            controller._native_click, element, preflight=preflight
        ),
    )


@pytest.mark.parametrize("changed", ["label", "role"])
def test_semantic_identity_changes_during_hit_test_refuse_click(changed):
    controller, _, element, mouse_calls = fixture()
    element.on_hit = lambda: element._attrs.update(
        aria_label="Delete" if changed == "label" else "Continue",
        role="checkbox" if changed == "role" else "button",
    )
    assert_no_input(controller, element, mouse_calls, lambda: controller.click(1))


def test_revocation_after_hit_test_refuses_input():
    controller, _, element, mouse_calls = fixture()
    stopped = [False]

    def guard():
        if stopped[0]:
            raise RuntimeError("request revoked")

    controller.input_guard = guard
    element.on_hit = lambda: stopped.__setitem__(0, True)
    assert_no_input(controller, element, mouse_calls, lambda: controller.click(1))


@pytest.mark.parametrize(
    "box",
    [
        None,
        {"x": float("nan"), "y": 120, "width": 100, "height": 40},
        {"x": 200, "y": 120, "width": 0, "height": 40},
        {"x": -1, "y": 120, "width": 100, "height": 40},
        {"x": 900, "y": 120, "width": 100, "height": 40},
    ],
)
def test_missing_or_invalid_point_proof_never_dispatches_input(box):
    controller, _, element, mouse_calls = fixture()
    element.box = box
    assert_no_input(controller, element, mouse_calls, lambda: controller.click(1))
