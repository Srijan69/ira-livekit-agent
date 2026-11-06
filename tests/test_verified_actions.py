"""Verified actions drive only isolated Selenium/PyAutoGUI boundary fakes."""

import json
from types import SimpleNamespace

import pytest

from browser import BrowserController
from desktop import DesktopController
from skill_adapters import SkillToolAdapters
from skill_execution import SkillExecutionService
from skill_format import Action, Check, Skill, SkillInput, Step
from skill_runs import SkillRunStore
from telemetry import SessionStatusReporter

URL = "https://example.test/form"


class Element:
    def __init__(self, *, tag="input", displayed=True, enabled=True, **attrs):
        self.tag_name = tag
        self.displayed = displayed
        self.enabled = enabled
        self.attrs = {"type": "text", "value": "", **attrs}
        self.actions = []
        self.ignore_input = False
        self.stale = False

    def is_displayed(self):
        if self.stale:
            raise RuntimeError("stale element")
        return self.displayed

    def is_enabled(self):
        return self.enabled

    def get_attribute(self, name):
        return self.attrs.get(name)

    def get_dom_attribute(self, name):
        return self.attrs.get(name)

    def get_property(self, name):
        return self.attrs.get(name)

    def clear(self):
        self.actions.append("clear")
        self.attrs["value"] = ""

    def send_keys(self, text):
        self.actions.append(("type", text))
        if not self.ignore_input:
            self.attrs["value"] += text

    def click(self):
        self.actions.append("click")


class Driver:
    def __init__(self, *elements):
        self.current_url = URL
        self.elements = list(elements)
        self.selectors = []
        self.on_find = None

    def find_elements(self, by, selector):
        assert by == "css selector"
        self.selectors.append(selector)
        if self.on_find:
            self.on_find()
        return list(self.elements)

    def execute_script(self, *args):
        pytest.fail("Verified actions must not fall back to JavaScript")


def browser_with(*elements):
    browser = BrowserController()
    browser._driver = Driver(*elements)
    return browser


class DesktopBackend:
    def __init__(self):
        self.window = SimpleNamespace(
            title="Notes", left=100, top=50, width=300, height=200
        )
        self.actions = []
        self.on_size = None

    def getActiveWindow(self):  # noqa: N802 - PyAutoGUI's public API
        return self.window

    def size(self):
        if self.on_size:
            self.on_size()
        return 800, 600

    def click(self, x, y, *, button):
        self.actions.append(("click", x, y, button))

    def write(self, text, *, interval):
        self.actions.append(("write", text, interval))

    def press(self, key):
        self.actions.append(("press", key))


def test_selector_click_uses_a_fresh_target_and_ignores_legacy_indices():
    stale, current = Element(), Element(tag="button")
    browser = browser_with(current)
    browser._elements = [stale]
    browser.click_selector("#go", URL)
    assert current.actions == ["click"]
    assert stale.actions == []
    replacement = Element(tag="button")
    browser._driver.elements = [replacement]
    browser.click_selector("#go", URL)
    assert replacement.actions == ["click"]


@pytest.mark.parametrize(
    "elements",
    [[], [Element(), Element()], [Element(displayed=False)], [Element(enabled=False)]],
)
def test_selector_click_rejects_missing_ambiguous_hidden_and_disabled_targets(elements):
    browser = browser_with(*elements)
    with pytest.raises((ValueError, RuntimeError)):
        browser.click_selector("#go", URL)
    assert all(not element.actions for element in elements)


def test_stale_target_does_not_retry_or_rebind():
    stale = Element()
    stale.stale = True
    browser = browser_with(stale)
    with pytest.raises(RuntimeError):
        browser.click_selector("#go", URL)
    assert stale.actions == []


def test_intercepted_click_does_not_fall_back_to_javascript():
    class Intercepted(Element):
        def click(self):
            raise RuntimeError("intercepted")

    browser = browser_with(Intercepted())
    with pytest.raises(RuntimeError):
        browser.click_selector("#go", URL)


@pytest.mark.parametrize(
    "actual",
    ["https://example.test/other", URL + "?changed=1", "https://different.test/form"],
)
def test_click_rejects_url_drift_before_finding_targets(actual):
    element = Element()
    browser = browser_with(element)
    browser._driver.current_url = actual
    with pytest.raises(ValueError, match="URL"):
        browser.click_selector("#go", URL)
    assert browser._driver.selectors == []
    assert element.actions == []


def test_click_canonicalizes_host_default_port_and_fragment():
    element = Element()
    browser = browser_with(element)
    browser._driver.current_url = "https://EXAMPLE.test:443/#now"
    browser.click_selector("#go", "https://example.test#before")
    assert element.actions == ["click"]


def test_click_rechecks_url_after_resolving_target():
    element = Element()
    browser = browser_with(element)
    browser._driver.on_find = lambda: setattr(
        browser._driver, "current_url", URL + "?drift"
    )
    with pytest.raises(ValueError, match="URL"):
        browser.click_selector("#go", URL)
    assert element.actions == []


def test_url_scope_does_not_collapse_browser_distinct_unicode_hostnames():
    element = Element()
    browser = browser_with(element)
    browser._driver.current_url = "https://fass.test/form"
    with pytest.raises(ValueError, match="URL"):
        browser.click_selector("#go", "https://fa\u00df.test/form")
    assert element.actions == []


def test_browser_type_verifies_value_before_submitting_and_keeps_text_private():
    element = Element()
    adapters = SkillToolAdapters(
        browser_with(element), DesktopController(mouse=DesktopBackend()), lambda _: None
    )
    outcome = adapters.browser_type("#query", "private search", URL, submit=True)
    assert outcome.ok
    assert element.actions == ["clear", ("type", "private search"), ("type", "\ue006")]
    assert "private search" not in str(outcome)


def test_browser_type_cannot_submit_when_input_value_does_not_match():
    element = Element()
    element.ignore_input = True
    browser = browser_with(element)
    with pytest.raises(RuntimeError, match="verif") as error:
        browser.type_selector("#query", "private search", URL, submit=True)
    assert element.actions == ["clear", ("type", "private search")]
    assert "private search" not in str(error.value)


@pytest.mark.parametrize(
    "attrs",
    [
        {"type": "password"},
        {"type": "file"},
        {"type": "hidden"},
        {"readonly": "true"},
        {"readonly": ""},
    ],
)
def test_browser_type_rejects_unsafe_fields_without_reading_or_sending_text(attrs):
    class Unreadable(Element):
        def get_property(self, name):
            assert name != "value", "Password contents must never be read"
            return super().get_property(name)

    element = Unreadable(**attrs)
    with pytest.raises(ValueError):
        browser_with(element).type_selector("#field", "private text", URL)
    assert element.actions == []


def test_browser_type_rejects_replaced_target_after_typing():
    element, replacement = Element(), Element(value="value")
    browser = browser_with(element)
    original_send = element.send_keys

    def replace_on_type(text):
        original_send(text)
        browser._driver.elements = [replacement]

    element.send_keys = replace_on_type
    with pytest.raises(RuntimeError):
        browser.type_selector("#field", "value", URL, submit=True)
    assert replacement.actions == []


@pytest.mark.parametrize("new_matches", ["replacement", "ambiguous", "missing"])
def test_browser_type_resolves_same_unique_target_after_clearing(new_matches):
    element, other = Element(), Element()
    browser = browser_with(element)
    original_clear = element.clear

    def change_matches_on_clear():
        original_clear()
        browser._driver.elements = {
            "replacement": [other],
            "ambiguous": [element, other],
            "missing": [],
        }[new_matches]

    element.clear = change_matches_on_clear
    with pytest.raises(RuntimeError):
        browser.type_selector("#field", "private text", URL, submit=True)
    assert element.actions == ["clear"]
    assert other.actions == []


def test_page_value_observation_is_exact_fresh_and_redacted():
    element = Element(value="current value")
    adapters = SkillToolAdapters(browser_with(element), None, None)
    check = Check(
        "page_value", json.dumps({"selector": "#field", "value": "current value"})
    )
    outcome = adapters.verify(check)
    assert outcome.ok
    assert "current value" not in outcome.evidence
    element.attrs["value"] = "changed"
    assert not adapters.verify(check).ok


@pytest.mark.parametrize(
    "elements",
    [
        [Element(type="password", value="secret")],
        [Element(), Element()],
        [Element(displayed=False)],
        [],
    ],
)
def test_page_value_never_accepts_password_ambiguous_or_hidden_fields(elements):
    browser = browser_with(*elements)
    with pytest.raises((ValueError, RuntimeError)):
        browser.observe("page_value", '{"selector":"#field","value":"secret"}')


def test_desktop_click_requires_exact_foreground_title():
    backend = DesktopBackend()
    desktop = DesktopController(mouse=backend)
    with pytest.raises(ValueError, match="foreground"):
        desktop.click_in_window(150, 100, "Note")
    assert backend.actions == []
    desktop.click_in_window(150, 100, "NOTES")
    assert backend.actions == [("click", 150, 100, "left")]


@pytest.mark.parametrize(
    "x,y", [(99, 100), (400, 100), (150, 49), (150, 250), (-1, 100), (800, 100)]
)
def test_desktop_click_requires_screen_and_foreground_window_bounds(x, y):
    backend = DesktopBackend()
    with pytest.raises(ValueError):
        DesktopController(mouse=backend).click_in_window(x, y, "Notes")
    assert backend.actions == []


def test_desktop_click_rechecks_foreground_identity_after_screen_lookup():
    backend = DesktopBackend()
    backend.on_size = lambda: setattr(
        backend,
        "window",
        SimpleNamespace(title="Notes", left=100, top=50, width=300, height=200),
    )
    with pytest.raises(ValueError, match="foreground"):
        DesktopController(mouse=backend).click_in_window(150, 100, "Notes")
    assert backend.actions == []


def test_desktop_click_rejects_another_window_covering_the_point(monkeypatch):
    backend = DesktopBackend()
    backend.window._hWnd = 123
    monkeypatch.setattr(
        "desktop._point_is_in_window_win32", lambda x, y, handle: False, raising=False
    )
    with pytest.raises(ValueError):
        DesktopController(mouse=backend).click_in_window(150, 100, "Notes")
    assert backend.actions == []


def test_desktop_typing_and_keys_require_focus_and_keep_text_private():
    backend = DesktopBackend()
    adapters = SkillToolAdapters(None, DesktopController(mouse=backend), None)
    outcome = adapters.desktop_type("private text", "Notes")
    assert outcome.ok and "private text" not in str(outcome)
    adapters.desktop_press_key("enter", "Notes")
    backend.window.title = "Other"
    with pytest.raises(ValueError, match="foreground"):
        adapters.desktop_type("no", "Notes")
    with pytest.raises(ValueError, match="foreground"):
        adapters.desktop_press_key("enter", "Notes")
    assert backend.actions == [("write", "private text", 0.0), ("press", "enter")]


def test_active_window_observation_requires_exact_casefold_title():
    backend = DesktopBackend()
    adapters = SkillToolAdapters(None, DesktopController(mouse=backend), None)
    assert adapters.verify(Check("active_window", "NOTES")).ok
    assert not adapters.verify(Check("active_window", "Note")).ok
    backend.window = None
    assert not adapters.verify(Check("active_window", "Notes")).ok


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("browser_click", {"selector": "1", "expected_url": URL}),
        ("browser_click", {"selector": "", "expected_url": URL}),
        ("browser_click", {"selector": "#go", "expected_url": "javascript:alert(1)"}),
        (
            "browser_type",
            {"selector": "#field", "text": "secret\ue007", "expected_url": URL},
        ),
        (
            "browser_type",
            {"selector": "#field", "text": "secret\n", "expected_url": URL},
        ),
        (
            "browser_type",
            {"selector": "#field", "text": "x" * 4001, "expected_url": URL},
        ),
        ("desktop_click", {"x": -(2**31) - 1, "y": 10, "window_title": "Notes"}),
        (
            "desktop_click",
            {"x": 10, "y": 10, "window_title": "Notes", "button": "back"},
        ),
        ("desktop_type", {"text": "line\nnext", "window_title": "Notes"}),
        ("desktop_type", {"text": "unicode \u20b9", "window_title": "Notes"}),
        ("desktop_type", {"text": "x" * 4001, "window_title": "Notes"}),
        ("desktop_press_key", {"key": "ctrl+v", "window_title": "Notes"}),
        ("desktop_press_key", {"key": "enter", "window_title": ""}),
    ],
)
def test_action_semantic_validation_has_no_browser_or_desktop_effects(name, arguments):
    registry = SkillToolAdapters(None, None, None).registry()
    assert name in registry.names()
    with pytest.raises(ValueError):
        registry.validate(name, arguments)


def test_symbolic_text_does_not_skip_validation_of_later_constant_arguments():
    registry = SkillToolAdapters(None, None, None).registry()
    assert "browser_type" in registry.names()
    with pytest.raises(ValueError):
        registry.validate(
            "browser_type",
            {
                "selector": "#field",
                "text": "{answer}",
                "expected_url": "file:///private",
            },
            symbolic=True,
        )
    with pytest.raises(ValueError):
        registry.validate(
            "desktop_click",
            {"x": "{x}", "y": "10", "window_title": "Notes", "button": "back"},
            symbolic=True,
        )


@pytest.mark.parametrize(
    "value",
    [
        "not json",
        "[]",
        '{"selector":"#field"}',
        '{"selector":"#field","value":2}',
        '{"selector":"","value":""}',
        '{"selector":"#field","value":"","other":1}',
    ],
)
def test_page_value_rejects_malformed_observation_specs(value):
    with pytest.raises(ValueError):
        browser_with(Element()).observe("page_value", value)


def test_observation_preflight_defers_only_unresolved_fields():
    check = Check("page_value", '{"selector":"#field","value":"{answer}"}')
    SkillToolAdapters.validate_check(check, symbolic=frozenset({"value"}))
    check = Check("page_value", '{"selector":"{selector}","value":"answer"}')
    SkillToolAdapters.validate_check(check, symbolic=frozenset({"selector"}))
    check = Check("page_value", '{"selector":"1","value":"{answer}"}')
    with pytest.raises(ValueError):
        SkillToolAdapters.validate_check(check, symbolic=frozenset({"value"}))
    with pytest.raises(ValueError):
        SkillToolAdapters.validate_check(
            Check("page_value", "{entire_spec}"), symbolic=frozenset({"value"})
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["validate", "run"])
async def test_late_invalid_page_selector_rejects_before_effects_or_question(
    tmp_path, mode
):
    element = Element(tag="button")
    browser = browser_with(element)
    adapters = SkillToolAdapters(browser, None, None)
    registry = adapters.registry()
    service = SkillExecutionService(
        verifier=adapters.verify,
        check_validator=adapters.validate_check,
        run_store=SkillRunStore(tmp_path / "runs.db"),
    )
    skill = Skill(
        "Validate before effects",
        "Reject invalid future check selectors before clicking",
        (
            Step(
                1,
                "Click",
                action=Action(
                    "tool",
                    tool="browser_click",
                    args=(("selector", "#go"), ("expected_url", URL)),
                ),
                check=Check("page_element", "#go"),
            ),
            Step(2, "Ask", action=Action("ask", param="answer", text="What text?")),
            Step(
                3,
                "Type",
                action=Action(
                    "tool",
                    tool="browser_type",
                    args=(
                        ("selector", "#field"),
                        ("text", "{answer}"),
                        ("expected_url", URL),
                    ),
                ),
                check=Check("page_element", "???"),
            ),
        ),
        inputs=(SkillInput("answer", "Text to enter"),),
        allowed_tools=("browser_click", "browser_type"),
        state="enabled",
    )
    try:
        if mode == "validate":
            with pytest.raises(ValueError, match="selector"):
                service.validate(skill, registry)
        else:
            result = await service.run(
                skill, registry, SessionStatusReporter(sink=lambda _: None)
            )
            assert result.status == "error"
        assert element.actions == []
        assert browser._driver.selectors == []
    finally:
        await service.aclose()


@pytest.mark.parametrize(
    "selector",
    [
        "#query",
        "input[name='q']",
        "form > input.search",
        'button[aria-label="Save draft"]',
        "#go, button.save",
        "li:nth-child(2) a",
        "input:enabled",
    ],
)
def test_supported_selectors_can_be_validated_without_opening_a_browser(selector):
    registry = SkillToolAdapters(None, None, None).registry()
    assert (
        registry.validate("browser_click", {"selector": selector, "expected_url": URL})[
            "selector"
        ]
        == selector
    )


@pytest.mark.parametrize(
    "selector",
    ["input[", "#go; alert(1)", "//input", "button >", "input[]", ":has(input)"],
)
def test_bad_constant_selectors_fail_even_with_unresolved_text(selector):
    registry = SkillToolAdapters(None, None, None).registry()
    with pytest.raises(ValueError):
        registry.validate(
            "browser_type",
            {"selector": selector, "text": "{answer}", "expected_url": URL},
            symbolic=True,
        )


@pytest.mark.parametrize(
    "url",
    [
        "https://user:pass@example.test/form",
        "https://example.test:99999/form",
        "https://example.test\\@other.test/form",
        "https://bad*host/form",
        "https://example.test/form\n",
        "example.test/form",
    ],
)
def test_bad_constant_url_scope_fails_even_with_unresolved_text(url):
    registry = SkillToolAdapters(None, None, None).registry()
    with pytest.raises(ValueError):
        registry.validate(
            "browser_type",
            {"selector": "#field", "text": "{answer}", "expected_url": url},
            symbolic=True,
        )
