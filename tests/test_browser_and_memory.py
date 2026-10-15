"""Tests for the browser and memory modules.

Nothing here starts a real Chrome or touches the real memory database. The
browser tests drive `BrowserController` through a fake driver injected straight
into `_driver`, and the memory tests get a `tmp_path` database. That is a hard
rule rather than a convenience: these tools act on the boss's actual machine,
and a test suite that opens windows and saves facts is one nobody can run
while working.
"""

from __future__ import annotations

import pytest

from browser import (
    MAX_ELEMENTS,
    MAX_PAGE_TEXT,
    BrowserController,
    PageElement,
    _describe,
    browser_config,
)
from memory import MemoryStore

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeElement:
    def __init__(self, tag: str, *, text: str = "", displayed: bool = True, **attrs):
        self.tag_name = tag
        self.text = text
        self._displayed = displayed
        self._attrs = attrs
        self.clicked = False
        self.typed: list[str] = []
        self.cleared = False

    def get_attribute(self, name: str):
        return self._attrs.get(name.replace("-", "_"), self._attrs.get(name))

    def is_displayed(self) -> bool:
        return self._displayed

    def click(self) -> None:
        self.clicked = True

    def clear(self) -> None:
        self.cleared = True

    def send_keys(self, value) -> None:
        self.typed.append(str(value))


class FakeDriver:
    def __init__(self, elements=None, *, title="Example", body_text="Hello world"):
        self._elements = elements or []
        self.title = title
        self._body = FakeElement("body", text=body_text)
        self.visited: list[str] = []
        self.went_back = False
        self.quit_called = False
        self.scripts: list[str] = []
        self.script_results: list[object] = []

    def find_elements(self, by, selector):
        return list(self._elements)

    def find_element(self, by, selector):
        return self._body

    def get(self, url):
        self.visited.append(url)

    def back(self):
        self.went_back = True

    def quit(self):
        self.quit_called = True

    def execute_script(self, script, *args):
        self.scripts.append(script)
        return self.script_results.pop(0) if self.script_results else None

    def set_page_load_timeout(self, value):
        pass

    def implicitly_wait(self, value):
        pass


def _controller(driver: FakeDriver) -> BrowserController:
    controller = BrowserController(headless=True)
    controller._driver = driver
    return controller


# ---------------------------------------------------------------------------
# Element description -- what the model is actually shown
# ---------------------------------------------------------------------------


def test_describe_reads_roles_the_way_a_person_would_say_them():
    assert _describe(FakeElement("a", text="Sign in", href="/login"))[0] == "link"
    assert _describe(FakeElement("button", text="Submit"))[0] == "button"
    assert _describe(FakeElement("input", type="text"))[0] == "textbox"
    assert _describe(FakeElement("textarea"))[0] == "textbox"
    # An <input type=submit> is a button to anyone looking at it, whatever the
    # tag says. Calling it a textbox would invite the model to type into it.
    assert _describe(FakeElement("input", type="submit", value="Go"))[0] == "button"
    assert _describe(FakeElement("input", type="checkbox"))[0] == "checkbox"


def test_describe_falls_back_through_label_sources():
    """Visible text wins; then aria-label; then placeholder."""
    assert _describe(FakeElement("a", text="Docs"))[1] == "Docs"
    assert _describe(FakeElement("a", **{"aria-label": "Home"}))[1] == "Home"
    assert _describe(FakeElement("input", placeholder="Search"))[1] == "Search"
    # No label anywhere is still listed -- it may be the only way forward.
    role, label = _describe(FakeElement("button"))
    assert role == "button"
    assert label == ""


def test_element_labels_are_capped():
    """An aria-label can hold a paragraph; IRA should not read it aloud."""
    _, label = _describe(FakeElement("a", text="x" * 500))
    assert len(label) <= 80


def test_spoken_form_is_numbered():
    assert PageElement(3, "link", "Sign in").spoken() == "3. link Sign in"
    # No trailing space when there is no label.
    assert PageElement(1, "button", "").spoken() == "1. button"


# ---------------------------------------------------------------------------
# Enumeration and numbering
# ---------------------------------------------------------------------------


def test_list_elements_numbers_from_one_and_skips_hidden():
    driver = FakeDriver(
        [
            FakeElement("a", text="First"),
            FakeElement("a", text="Hidden", displayed=False),
            FakeElement("button", text="Second"),
        ]
    )
    found = _controller(driver).list_elements()
    assert [e.index for e in found] == [1, 2]
    assert [e.label for e in found] == ["First", "Second"]


def test_list_elements_is_capped():
    """A busy page has hundreds of links; the list has to stay speakable."""
    driver = FakeDriver([FakeElement("a", text=f"L{i}") for i in range(200)])
    assert len(_controller(driver).list_elements()) == MAX_ELEMENTS


def test_stale_elements_are_skipped_not_fatal():
    """Live pages mutate mid-enumeration. One bad element must not kill the list."""

    class Exploding(FakeElement):
        def is_displayed(self):
            raise RuntimeError("stale element reference")

    driver = FakeDriver(
        [FakeElement("a", text="Good"), Exploding("a"), FakeElement("a", text="Also")]
    )
    found = _controller(driver).list_elements()
    assert [e.label for e in found] == ["Good", "Also"]


# ---------------------------------------------------------------------------
# Acting by number
# ---------------------------------------------------------------------------


def test_click_acts_on_the_numbered_element():
    first = FakeElement("a", text="First")
    second = FakeElement("a", text="Second")
    controller = _controller(FakeDriver([first, second]))
    controller.list_elements()

    result = controller.click(2)
    assert second.clicked is True
    assert first.clicked is False
    assert "Second" in result


def test_click_falls_back_to_javascript_when_intercepted():
    """Cookie banners intercept real clicks constantly. One retry is worth it."""

    class Intercepted(FakeElement):
        def click(self):
            raise RuntimeError("element click intercepted")

    element = Intercepted("a", text="Accept")
    driver = FakeDriver([element])
    controller = _controller(driver)
    controller.list_elements()

    controller.click(1)
    assert driver.scripts, "expected a JS click fallback"


def test_click_out_of_range_explains_itself():
    controller = _controller(FakeDriver([FakeElement("a", text="Only")]))
    controller.list_elements()
    with pytest.raises(IndexError) as excinfo:
        controller.click(7)
    # The message goes to the model, which has to say something useful.
    assert "7" in str(excinfo.value)
    assert "1" in str(excinfo.value)


def test_click_invalidates_the_numbering():
    """Clicking changes the page, so old indices must not survive it.

    Stale numbering is the main failure mode of this style of tool: saying
    "click three" and getting whatever is third on a page that already moved.
    """
    controller = _controller(FakeDriver([FakeElement("a", text="Go")]))
    controller.list_elements()
    assert controller._elements
    controller.click(1)
    assert controller._elements == []


def test_type_text_clears_first_and_can_submit():
    box = FakeElement("input", type="text", placeholder="Search")
    controller = _controller(FakeDriver([box]))
    controller.list_elements()

    controller.type_text(1, "livekit agents")
    assert box.cleared is True
    assert "livekit agents" in box.typed

    controller.type_text(1, "again", submit=True)
    assert len(box.typed) > 2, "expected a RETURN keypress after the text"


# ---------------------------------------------------------------------------
# Navigation and reading
# ---------------------------------------------------------------------------


def test_navigate_assumes_https_for_spoken_addresses():
    """Speech never includes a scheme -- "go to example dot com" has no https."""
    driver = FakeDriver()
    _controller(driver).navigate("example.com")
    assert driver.visited == ["https://example.com"]


def test_navigate_keeps_an_explicit_scheme():
    driver = FakeDriver()
    _controller(driver).navigate("http://localhost:8080")
    assert driver.visited == ["http://localhost:8080"]


def test_navigate_rejects_an_empty_address():
    driver = FakeDriver()
    assert "No address" in _controller(driver).navigate("   ")
    assert driver.visited == []


def test_read_page_is_capped():
    """Page text is spoken aloud and lands in context on every later turn."""
    driver = FakeDriver(body_text="word " * 5000)
    text = _controller(driver).read_page()
    assert len(text) <= MAX_PAGE_TEXT + 20
    assert text.endswith("(truncated)")


def test_describe_page_reports_where_we_are():
    driver = FakeDriver([FakeElement("a", text="One")], title="Hacker News")
    assert _controller(driver).describe_page() == "Hacker News. 1 things to act on."


def test_close_is_safe_when_nothing_is_open():
    controller = BrowserController(headless=True)
    assert "not open" in controller.close()


def test_close_quits_and_forgets_the_driver():
    driver = FakeDriver()
    controller = _controller(driver)
    controller.close()
    assert driver.quit_called is True
    assert controller.is_open is False


def test_browser_config_uses_an_unlocked_persistent_ira_profile(monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\boss\AppData\Local")
    config = browser_config("chrome")
    assert config["browser"] == "chrome"
    assert config["user_data_dir"].endswith(r"IRA\Browser\Chrome")
    assert config["profile_directory"] == "Default"


def test_browser_config_supports_brave_and_an_explicit_profile(monkeypatch, tmp_path):
    monkeypatch.setenv("IRA_BROWSER_PROFILE", str(tmp_path / "Profile 2"))
    config = browser_config("brave")
    assert config["browser"] == "brave"
    assert config["user_data_dir"] == str(tmp_path)
    assert config["profile_directory"] == "Profile 2"


def test_browser_config_does_not_use_the_live_chrome_profile(monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\boss\AppData\Local")
    config = browser_config("chrome")
    assert "Google\\Chrome\\User Data" not in config["user_data_dir"]


def test_spotify_controls_use_the_current_player_audio():
    driver = FakeDriver()
    driver.script_results = ["playing", "paused", 87.0]
    controller = _controller(driver)
    assert controller.spotify_control("play") == "Spotify play requested."
    assert controller.spotify_control("pause") == "Spotify pause requested."
    assert controller.spotify_control("rewind", 13) == "Spotify rewound 13 seconds."
    assert len(driver.scripts) == 3
    assert "currentTime" in driver.scripts[-1]


def test_spotify_playlist_search_selects_match_and_verifies_playback():
    driver = FakeDriver()
    driver.script_results = ["matched", "playing"]
    controller = _controller(driver)

    result = controller.spotify_play_playlist("My Funk Songs")

    assert driver.visited == [
        "https://open.spotify.com/search/My%20Funk%20Songs/playlists"
    ]
    assert "My Funk Songs" in result
    assert "playing" in result.lower()
    assert len(driver.scripts) == 2


def test_open_spotify_uses_the_selected_browser():
    driver = FakeDriver([FakeElement("button", text="Play")], title="Spotify")
    controller = _controller(driver)
    assert "Spotify" in controller.open_spotify("chrome")
    assert driver.visited == ["https://open.spotify.com"]


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(tmp_path / "test_memory.db")
    yield s
    s.close()


def test_remember_then_recall(store):
    store.remember("The boss takes his coffee black")
    hits = store.recall("coffee")
    assert len(hits) == 1
    assert "coffee" in hits[0].content


def test_recall_finds_related_words_not_just_exact_ones(store):
    """OR-ed terms, so a question phrased differently still finds the fact."""
    store.remember("The boss prefers dark roast coffee", kind="preference")
    assert store.recall("what does the boss drink")


def test_remember_is_idempotent(store):
    """Re-saying a known fact must not create a second row."""
    store.remember("The boss lives in Hyderabad")
    second = store.remember("the boss lives in hyderabad")
    assert store.count() == 1
    assert "Already" in second


def test_remember_rejects_empty_content(store):
    store.remember("   ")
    assert store.count() == 0


def test_remember_caps_length_and_collapses_whitespace(store):
    store.remember("a  b\n\nc " + "x" * 2000)
    assert len(store.recall("a b c")[0].content) <= 500


def test_unknown_kinds_fall_back_rather_than_fragmenting(store):
    """Free-form kinds would splinter into synonyms that never match."""
    store.remember("Something", kind="not-a-real-kind")
    assert store.recall("Something")[0].kind == "fact"


def test_seed_is_capped(store):
    """The whole point of the seed/recall split -- context stays bounded."""
    for i in range(50):
        store.remember(f"Fact number {i}")
    assert len(store.seed()) <= 12


def test_seed_prefers_preferences_and_proven_facts(store):
    store.remember("Rarely useful trivia")
    store.remember("The boss hates being read lists", kind="preference")
    # Exercise one fact so it has a use count behind it.
    store.remember("The boss works late")
    store.recall("works late")

    seeded = [m.content for m in store.seed(limit=2)]
    assert "The boss hates being read lists" in seeded


def test_preferences_are_available_as_a_dedicated_durable_view(store):
    store.remember("The boss prefers concise answers", kind="preference")
    store.remember("The boss lives in Hyderabad", kind="fact")
    store.close()
    reopened = MemoryStore(store._path)
    try:
        preferences = reopened.preferences()
        assert [item.content for item in preferences] == [
            "The boss prefers concise answers"
        ]
    finally:
        reopened.close()


def test_recall_promotes_what_it_returns(store):
    store.remember("The boss flies out on Tuesday", kind="event")
    store.recall("Tuesday")
    store.recall("Tuesday")
    assert store.recall("Tuesday", touch=False)[0].use_count >= 2


def test_recall_on_empty_query_returns_nothing(store):
    store.remember("Anything")
    assert store.recall("") == []


def test_recall_survives_fts_syntax_in_speech(store):
    """Bare AND/OR/NEAR and quotes are FTS5 syntax and would otherwise raise."""
    store.remember("The boss likes tea and coffee")
    assert store.recall('tea AND coffee "NEAR') is not None


def test_forget_removes_the_best_match(store):
    store.remember("The boss dislikes mushrooms")
    store.remember("The boss enjoys hiking")
    store.forget("mushrooms")
    assert store.count() == 1
    assert store.recall("mushrooms") == []


def test_forget_says_so_when_nothing_matches(store):
    assert "Nothing" in store.forget("something never stored")


def test_memory_survives_reopening(store, tmp_path):
    """It is durable storage or it is not memory."""
    store.remember("The boss's dog is called Rex")
    store.close()

    reopened = MemoryStore(tmp_path / "test_memory.db")
    try:
        assert reopened.recall("Rex")
    finally:
        reopened.close()
