import asyncio
import json
from types import SimpleNamespace

import pytest

from context_summary import ContextSummarizer, SummaryError, validate_summary


def summary(**sections):
    return {
        "overview": "The user is planning a project.",
        "facts": [],
        "decisions": [],
        "open_threads": [],
        "outcomes": [],
        "questions": [],
        **sections,
    }


def item(item_id="u1", **changes):
    return {
        "seq": 1,
        "item_id": item_id,
        "role": "user",
        "text": "I prefer Python.",
        "kind": "message",
        "trusted": True,
        "tool_call_id": None,
        "tool_name": None,
        "status": None,
        **changes,
    }


class GoogleBoundary:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []
        self.closed = False
        self.aio = SimpleNamespace(
            models=SimpleNamespace(generate_content=self.generate_content),
            aclose=self.aclose,
        )

    async def generate_content(self, **request):
        self.requests.append(request)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        if callable(response):
            return await response()
        return SimpleNamespace(text=response)

    async def aclose(self):
        self.closed = True


async def test_google_request_uses_only_environment_key_and_disabled_tools(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "synthetic-user-key")
    boundary = GoogleBoundary([json.dumps(summary())])
    created = []

    def factory(**kwargs):
        created.append(kwargs)
        return boundary

    summarizer = ContextSummarizer(client_factory=factory)
    previous = summary(
        decisions=[{"content": "Use Python.", "source_item_ids": ["old"]}]
    )
    result = await summarizer.summarize(
        previous=previous,
        items=[item()],
        profile={"name": "Example"},
        model="text-model",
    )
    assert result["overview"] == "The user is planning a project."
    assert created == [{"api_key": "synthetic-user-key"}]
    request = boundary.requests[0]
    assert request["model"] == "text-model"
    assert request["config"].tools == []
    assert request["config"].automatic_function_calling.disable is True
    payload = json.loads(request["contents"])
    assert payload["previous"] == previous
    assert payload["items"][0]["item_id"] == "u1"
    assert payload["profile"] == {"name": "Example"}
    await summarizer.aclose()
    assert boundary.closed


async def test_missing_key_never_creates_provider_client(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(SummaryError, match="GOOGLE_API_KEY"):
        await ContextSummarizer().summarize(
            previous=None, items=[item()], profile={}, model="text-model"
        )


@pytest.mark.parametrize("response", ["not json", "[]", '{"overview":"only"}'])
async def test_malformed_response_is_rejected_without_checkpoint(response):
    boundary = GoogleBoundary([response])
    summarizer = ContextSummarizer(client=boundary, max_attempts=1)
    with pytest.raises(SummaryError):
        await summarizer.summarize(previous=None, items=[item()], profile={}, model="m")


@pytest.mark.parametrize(
    "section,entry,source",
    [
        (
            "decisions",
            {"content": "Invented decision.", "source_item_ids": ["alien"]},
            item(),
        ),
        (
            "facts",
            {"content": "The user prefers Rust.", "source_item_ids": ["u1"]},
            item(trusted=False),
        ),
        (
            "outcomes",
            {"content": "The build succeeded.", "source_item_ids": ["u1"]},
            item(role="tool", kind="tool_result", status="failed"),
        ),
        (
            "outcomes",
            {"content": "The build succeeded.", "source_item_ids": ["u1"]},
            item(role="assistant"),
        ),
    ],
)
def test_untrusted_invented_or_false_success_claim_is_rejected(section, entry, source):
    with pytest.raises(SummaryError):
        validate_summary(summary(**{section: [entry]}), previous=None, items=[source])


def test_verified_failed_outcome_and_prior_references_are_preserved():
    previous = summary(
        decisions=[{"content": "Use Python.", "source_item_ids": ["old"]}]
    )
    result = summary(
        decisions=previous["decisions"],
        outcomes=[{"content": "The build failed.", "source_item_ids": ["t1"]}],
    )
    assert (
        validate_summary(
            result,
            previous=previous,
            items=[item("t1", role="tool", kind="tool_result", status="failed")],
        )
        == result
    )


async def test_timeout_and_quota_error_retry_are_bounded():
    async def stalled():
        await asyncio.sleep(1)

    timeout = ContextSummarizer(
        client=GoogleBoundary([stalled]), timeout_seconds=0.01, max_attempts=1
    )
    with pytest.raises(SummaryError, match="timed out"):
        await timeout.summarize(previous=None, items=[item()], profile={}, model="m")
    boundary = GoogleBoundary([RuntimeError("quota"), json.dumps(summary())])
    retried = ContextSummarizer(client=boundary, max_attempts=2, retry_delay_seconds=0)
    assert (
        await retried.summarize(previous=None, items=[item()], profile={}, model="m")
    )["overview"]
    assert len(boundary.requests) == 2


async def test_oversized_input_and_output_are_rejected_without_truncating_sources():
    boundary = GoogleBoundary([json.dumps(summary(overview="x" * 1000))])
    summarizer = ContextSummarizer(
        client=boundary, max_input_tokens=1000, max_summary_tokens=100, max_attempts=1
    )
    with pytest.raises(SummaryError, match="input"):
        await summarizer.summarize(
            previous=None, items=[item(text="x" * 10000)], profile={}, model="m"
        )
    assert boundary.requests == []
    with pytest.raises(SummaryError, match="size"):
        await summarizer.summarize(previous=None, items=[], profile={}, model="m")
    assert len(boundary.requests) == 1


def test_failed_tool_cannot_hide_completion_inside_a_mixed_outcome():
    with pytest.raises(SummaryError):
        validate_summary(
            summary(
                outcomes=[
                    {
                        "content": "The tool failed but the deployment completed.",
                        "source_item_ids": ["t1"],
                    }
                ]
            ),
            previous=None,
            items=[item("t1", role="tool", kind="tool_result", status="failed")],
        )
