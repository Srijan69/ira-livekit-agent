"""Internal extractors use the marshalled browser API, never a raw driver."""

from image_pipeline import scrape_image_urls
from market_report import scrape_market_assets


def test_image_extractor_uses_browser_evaluate_and_supplied_count():
    calls = []

    class Browser:
        def evaluate(self, expression, arg=None):
            calls.append(arg)
            return ["https://example.com/actual-photo.jpg"]

    assert scrape_image_urls(browser=Browser(), count=1) == [
        "https://example.com/actual-photo.jpg"
    ]
    assert calls == [1]


def test_image_extractor_can_read_browser_html_after_script_failure():
    class Browser:
        def evaluate(self, expression, arg=None):
            raise RuntimeError("script unavailable")

        def page_html(self):
            return '<img src="https://example.com/fallback-photo.jpg">'

    assert scrape_image_urls(browser=Browser(), count=1) == [
        "https://example.com/fallback-photo.jpg"
    ]


def test_market_extractor_uses_browser_evaluate_and_normalizes_results():
    calls = []

    class Browser:
        is_open = True

        def evaluate(self, expression, arg=None):
            calls.append(arg)
            return [
                {"name": "Observed Asset", "price": "$123.45", "raw_change": "+2.0%"}
            ]

    result = scrape_market_assets(browser=Browser(), max_assets=1)
    assert result == [{"name": "Observed Asset", "price": "$123.45", "change_24h": 2.0}]
    assert calls == [1]


def test_market_extractor_can_read_browser_html_after_script_failure():
    class Browser:
        is_open = True

        def evaluate(self, expression, arg=None):
            raise RuntimeError("script unavailable")

        def page_html(self):
            return '<table><tr><td data-field="name">Observed Asset</td><td data-field="price">$123.45</td><td data-field="change">+2.0%</td></tr></table>'

    assert (
        scrape_market_assets(browser=Browser(), max_assets=1)[0]["name"]
        == "Observed Asset"
    )
