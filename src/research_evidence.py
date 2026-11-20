"""Bounded evidence of web pages read for the current research request."""

import re
from urllib.parse import urlsplit, urlunsplit


class ResearchEvidence:
    def __init__(self, request_id, request_text, subject):
        self.request_id = request_id
        self.subject = subject.strip()
        text = request_text.casefold()
        self.required_sources = (
            3 if re.search(r"\b(deep|detailed|comprehensive|thorough)\b", text) else 1
        )
        requested = re.search(
            r"\b(one|two|three|four|five|[1-9])\s+(?:independent\s+)?sources?\b", text
        )
        if requested:
            numbers = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
            value = requested.group(1)
            self.required_sources = numbers[value] if value in numbers else int(value)
        self.required_hosts = 2 if self.required_sources > 1 else 1
        if re.search(
            r"\b(?:only|exclusively)\s+(?:use\s+)?(?:official|primary|microsoft)\b",
            text,
        ):
            self.required_hosts = 1
        self._sources = {}

    def observe(self, url, text):
        if not isinstance(url, str) or not isinstance(text, str) or not text.strip():
            return
        target = urlsplit(url)
        host = (target.hostname or "").casefold().removeprefix("www.")
        if target.scheme not in {"http", "https"} or not host or target.username:
            return
        if host in {"bing.com", "google.com", "duckduckgo.com", "search.yahoo.com"}:
            return
        if text.strip() == "That page has no readable text.":
            return
        words = re.findall(r"\w+", self.subject.casefold())
        content = text.casefold() + " " + host
        if not words or not all(word in content for word in words):
            return
        canonical = urlunsplit(
            (target.scheme, target.netloc.casefold(), target.path, target.query, "")
        )
        if canonical not in self._sources and len(self._sources) >= 32:
            return
        self._sources[canonical] = {
            "url": canonical,
            "host": host,
            "title": " ".join(text.split())[:160],
        }

    def status(self):
        sources = [dict(source) for source in self._sources.values()]
        hosts = len({source["host"] for source in sources})
        return {
            "request_id": self.request_id,
            "subject": self.subject,
            "required_sources": self.required_sources,
            "independent_hosts": hosts,
            "sources": sources,
            "ready": len(sources) >= self.required_sources
            and hosts >= self.required_hosts,
            "instruction": "Use the actually read sources for the requested summary; report limitations and conflicting evidence honestly.",
        }
