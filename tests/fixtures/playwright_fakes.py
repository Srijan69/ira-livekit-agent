"""Small native Page/BrowserContext/ElementHandle boundaries for offline tests."""

import datetime
import re
from types import SimpleNamespace

from browser import BrowserController


class FakeElement:
    def __init__(self, tag="input", *, text="", displayed=True, enabled=True, **attrs):
        self.tag_name = tag
        self.text = text
        self._displayed = displayed
        self._enabled = enabled
        self._attrs = {"type": "text", "value": "", **attrs}
        self.actions = []
        self.clicked = False
        self.stale = False
        self.ignore_input = False
        self.options = []
        self.selected_index = 0
        self.on_fill = None
        self.on_click = None
        self.on_trial = None
        self.owner_page = None
        self.ordinary_root = True
        self.point_hit = True
        self.box = {"x": 10.0, "y": 10.0, "width": 100.0, "height": 20.0}

    def get_attribute(self, name):
        return self._attrs.get(name.replace("-", "_"), self._attrs.get(name))

    def is_visible(self):
        if self.stale:
            raise RuntimeError("detached node")
        return self._displayed

    def is_enabled(self):
        return self._enabled and self.get_attribute("disabled") is None

    def inner_text(self):
        return self.text

    def input_value(self):
        return self._attrs["value"]

    def evaluate(self, expression, arg=None):
        if "document.elementFromPoint" in expression:
            metadata = self.evaluate("metadata")
            owned = (
                self.point_hit
                and not self.stale
                and self.ordinary_root
                and self.box == arg[1]
                and [metadata["role"], metadata["label"]] == list(arg[2])
                and [metadata["tag"], metadata["type"]] == list(arg[3])
            )
            if owned and self.owner_page is not None:
                self.owner_page.proved_element = self
            return owned
        if "getRootNode" in expression:
            return self.ordinary_root
        if "document.createElement('input')" in expression:
            kind = self.get_attribute("type")
            try:
                if not arg or kind not in {"date", "datetime-local", "month", "time"}:
                    return arg
                if kind == "datetime-local":
                    datetime.datetime.fromisoformat(arg)
                    return arg.removesuffix(":00") if arg.endswith(":00") else arg
                if kind == "date":
                    datetime.date.fromisoformat(arg)
                elif kind == "month":
                    datetime.date.fromisoformat(arg + "-01")
                else:
                    datetime.time.fromisoformat(arg)
                return arg
            except ValueError:
                return ""
        if "href:node.href" in expression:
            return {
                "name": self.text or self.get_attribute("aria-label") or "",
                "href": self.get_attribute("href"),
            }
        if "node===other" in expression:
            return self is arg
        if "node.options" in expression:
            return [
                {
                    "index": i,
                    "text": o.text.strip(),
                    "value": o.value.strip(),
                    "disabled": o.disabled,
                }
                for i, o in enumerate(self.options)
            ]
        if "selectedIndex" in expression:
            return self.selected_index
        if "node.isConnected" in expression and "label:" not in expression:
            return not self.stale
        if "disabled:node.disabled" in expression:
            return {"tag": self.tag_name, "disabled": not self.is_enabled()}
        tag = self.tag_name
        kind = self.get_attribute("type") or "text"
        role = self.get_attribute("role") or (
            "button"
            if tag == "input" and kind in {"submit", "button", "reset", "image"}
            else kind
            if tag == "input" and kind in {"checkbox", "radio"}
            else "textbox"
            if tag in {"input", "textarea"}
            else "link"
            if tag == "a"
            else tag
        )
        label = (
            self.text
            or self.get_attribute("aria-label")
            or self.get_attribute("placeholder")
            or (self.get_attribute("value") if role == "button" else "")
            or self.get_attribute("title")
            or self.get_attribute("name")
            or ""
        )
        return {
            "tag": tag,
            "type": kind,
            "role": role,
            "label": label,
            "readonly": self.get_attribute("readonly") is not None,
            "connected": not self.stale,
        }

    def click(self, *, trial=False, timeout=None):
        if self.stale or not self.is_visible() or not self.is_enabled():
            raise RuntimeError("target changed or disabled")
        if trial:
            if self.on_trial:
                self.on_trial()
            return
        self.actions.append("click")
        self.clicked = True
        if self.on_click:
            self.on_click()

    def wait_for_element_state(self, state, *, timeout=None):
        if self.stale or not self.is_enabled():
            raise RuntimeError("target changed or disabled")

    def fill(self, text, *, timeout=None):
        kind = self.get_attribute("type")
        if text and kind in {"date", "datetime-local", "month", "time"}:
            try:
                if kind == "date":
                    datetime.date.fromisoformat(text)
                elif kind == "datetime-local":
                    datetime.datetime.fromisoformat(text)
                elif kind == "month":
                    if re.fullmatch(r"\d{4}-\d{2}", text) is None:
                        raise ValueError
                    datetime.date.fromisoformat(text + "-01")
                else:
                    datetime.time.fromisoformat(text)
            except ValueError:
                raise RuntimeError("Malformed value") from None
        self.actions.append(("fill", text))
        if not self.ignore_input:
            self._attrs["value"] = text
        if self.on_fill:
            self.on_fill(text)

    def press(self, key, *, timeout=None):
        self.actions.append(("press", key))

    def select_option(self, *, index, timeout=None):
        self.actions.append(("select", index))
        self.selected_index = index
        self._attrs["value"] = self.options[index].value

    def scroll_into_view_if_needed(self, *, timeout=None):
        pass

    def hover(self, *, timeout=None):
        pass

    def owner_frame(self):
        return self.owner_page

    def bounding_box(self):
        return None if self.box is None else dict(self.box)


class FakeOption:
    def __init__(self, text, value=None, *, disabled=False):
        self.text = text
        self.value = text if value is None else value
        self.disabled = disabled


class FakeSelectElement(FakeElement):
    def __init__(self, name="category", options=None, *, disabled=False):
        super().__init__("select", name=name, enabled=not disabled)
        self.options = (
            options
            if options is not None
            else [
                FakeOption("Choose category", ""),
                FakeOption("Engineering", "eng"),
                FakeOption("Design", "des"),
            ]
        )
        if self.options:
            self._attrs["value"] = self.options[0].value

    @property
    def selected_value(self):
        return self.input_value()


class FakeLocator:
    def __init__(self, page, selector, elements):
        self.page, self.selector, self.elements = page, selector, elements

    def element_handles(self):
        self.page.selectors.append(self.selector)
        if self.page.on_find:
            self.page.on_find()
        elements = list(self.elements() if callable(self.elements) else self.elements)
        for element in elements:
            element.owner_page = self.page
        return elements

    def inner_text(self):
        return self.element_handles()[0].inner_text()

    def nth(self, index):
        return FakeLocator(
            self.page, self.selector, lambda: self.element_handles()[index : index + 1]
        )

    def element_handle(self):
        elements = self.element_handles()
        return elements[0] if elements else None


class FakeMouse:
    def __init__(self, page):
        self.page = page

    def click(self, x, y):
        self.page.mouse_actions.append(("click", x, y))
        element = self.page.proved_element
        if element is not None:
            element.actions.append("click")
            element.clicked = True
            if element.on_click:
                element.on_click()


class FakePage:
    def __init__(
        self,
        elements=None,
        *,
        title="Example",
        url="https://example.test/form",
        body_text="Hello world",
    ):
        self.elements = [] if elements is None else elements
        self._title = title
        self.url = url
        self.body = FakeElement("body", text=body_text)
        self.frames = [self]
        self.main_frame = self
        self.context = None
        self.closed = False
        self.visited = []
        self.selectors = []
        self.scripts = []
        self.script_results = []
        self.on_find = None
        self.on_goto = None
        self.evaluator = None
        self.went_back = False
        self.selector_map = {}
        self.proved_element = None
        self.mouse_actions = []
        self.mouse = FakeMouse(self)
        self.viewport_size = {"width": 1280, "height": 900}
        if not callable(self.elements):
            for element in self.elements:
                element.owner_page = self

    def title(self):
        return self._title

    def is_closed(self):
        return self.closed

    def on(self, event, callback):
        pass

    def goto(self, url, *, wait_until=None):
        self.visited.append(url)
        self.url = url
        if self.on_goto:
            self.on_goto(url)

    def go_back(self, *, wait_until=None):
        self.went_back = True

    def bring_to_front(self):
        if self.context:
            self.context.active_page = self

    def close(self):
        self.closed = True

    def locator(self, selector):
        if selector == "body":
            elements = [self.body]
        elif selector in self.selector_map:
            elements = self.selector_map[selector]
        else:

            def elements():
                return self.elements

        return FakeLocator(self, selector, elements)

    def evaluate(self, expression, arg=None):
        self.scripts.append(expression)
        if "width:innerWidth,height:innerHeight" in expression:
            return self.viewport_size
        if self.evaluator:
            return self.evaluator(expression, arg)
        return self.script_results.pop(0) if self.script_results else None

    def content(self):
        return "<body>" + self.body.text + "</body>"

    def wait_for_timeout(self, milliseconds):
        pass


class FakeContext:
    def __init__(self, pages=None):
        self.pages = pages or [FakePage()]
        self.active_page = self.pages[0]
        self.closed = False
        for page in self.pages:
            page.context = self

    def new_page(self):
        page = FakePage(title="New Tab", url="about:blank")
        page.context = self
        self.pages.append(page)
        self.active_page = page
        return page

    def on(self, event, callback):
        pass

    def set_default_timeout(self, value):
        pass

    def set_default_navigation_timeout(self, value):
        pass

    def close(self):
        self.closed = True
        for page in self.pages:
            page.close()


def controller_with(page=None, *, context=None):
    context = context or FakeContext([page or FakePage()])
    runtime = SimpleNamespace(
        chromium=SimpleNamespace(
            launch_persistent_context=lambda *a, **k: FakeContext()
        ),
        stop=lambda: None,
    )
    controller = BrowserController(headless=True, playwright_factory=lambda: runtime)

    def attach():
        controller._context = context
        controller._page = context.pages[0]
        for item in context.pages:
            controller._register_page(item)

    controller._worker.run(attach)
    return controller
