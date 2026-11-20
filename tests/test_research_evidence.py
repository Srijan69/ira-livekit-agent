"""Research outcomes require actually read, relevant sources."""

from research_evidence import ResearchEvidence


def test_deep_research_does_not_finish_after_a_search_page_or_opened_site():
    evidence = ResearchEvidence(
        "request-1", "Do a deep search about Microsoft", "Microsoft"
    )
    evidence.observe(
        "https://www.bing.com/search?q=Microsoft", "Microsoft search results"
    )
    evidence.observe(
        "https://www.microsoft.com/about", "That page has no readable text."
    )
    assert evidence.status()["ready"] is False
    assert evidence.status()["sources"] == []


def test_research_reports_read_sources_and_deduplicates_them():
    evidence = ResearchEvidence(
        "request-1", "Do a deep search about Microsoft", "Microsoft"
    )
    for url, text in [
        ("https://www.microsoft.com/about", "Microsoft company overview"),
        ("https://www.microsoft.com/about#history", "Microsoft company overview"),
        ("https://www.microsoft.com/investor", "Microsoft financial results"),
        (
            "https://www.reuters.com/technology/microsoft",
            "Microsoft latest developments",
        ),
    ]:
        evidence.observe(url, text)
    status = evidence.status()
    assert status["ready"] is True
    assert len(status["sources"]) == 3
    assert status["independent_hosts"] == 2
    assert status["sources"][0]["url"] == "https://www.microsoft.com/about"


def test_explicit_one_source_request_overrides_deep_research_default():
    evidence = ResearchEvidence(
        "request-2", "Deep research Microsoft using only one source", "Microsoft"
    )
    evidence.observe("https://www.microsoft.com/about", "Microsoft company overview")
    assert evidence.status()["ready"] is True
    assert evidence.status()["required_sources"] == 1


def test_unrelated_or_empty_pages_are_not_research_evidence():
    evidence = ResearchEvidence("request-1", "Search about Microsoft", "Microsoft")
    evidence.observe("https://open.spotify.com", "Play your favorite playlist")
    evidence.observe("https://example.org", "")
    assert evidence.status()["sources"] == []


def test_read_only_research_about_spotify_remains_supported():
    evidence = ResearchEvidence("request-2", "Research Spotify as a company", "Spotify")
    evidence.observe("https://investors.spotify.com", "Spotify business results")
    assert evidence.status()["ready"] is True
