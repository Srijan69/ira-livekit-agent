"""Admission uses URLs and controls actually observed in the current page."""

from tests.fixtures.playwright_fakes import FakeElement, FakePage, controller_with


def test_scope_observation_exposes_current_source_links_and_control_identity():
    element = FakeElement(
        tag="a",
        href="https://en.wikipedia.org/wiki/Microsoft",
        **{"aria-label": "Microsoft overview"},
    )
    browser = controller_with(
        FakePage([element], url="https://www.bing.com/search?q=Microsoft")
    )
    try:
        browser.list_elements()
        observation = browser.scope_observation()
        assert observation["url"] == "https://www.bing.com/search?q=Microsoft"
        assert observation["links"] == [
            {"url": "https://en.wikipedia.org/wiki/Microsoft"}
        ]
        assert observation["browser_targets"][0]["index"] == 1
    finally:
        browser.close()
