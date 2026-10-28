"""The SDK builds the right requests and parses the replies. No server, database or LLM needed."""

import json
from datetime import UTC, datetime

import httpx
import pytest

from agentic_memory.client import Memory

WHEN = datetime(2026, 10, 1, tzinfo=UTC)


def client(handler) -> Memory:
    return Memory("aman", "http://t", "secret", transport=httpx.MockTransport(handler))


def test_add_sends_the_round_with_key_and_a_stable_timestamp():
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"steps": [{"fact": "f", "action": "ADD"}]})

    steps = client(handler).add("I moved to Pune.", occurred_at=WHEN)

    assert steps == [{"fact": "f", "action": "ADD"}]
    (request,) = seen
    assert request.url.path == "/memories:ingest"
    assert request.headers["x-api-key"] == "secret"
    body = json.loads(request.content)
    assert body["user_id"] == "aman" and body["user_text"] == "I moved to Pune."
    assert body["occurred_at"] == WHEN.isoformat()  # a retry with the same value is idempotent


def test_search_parses_the_result_and_passes_options():
    def handler(request):
        body = json.loads(request.content)
        assert body["rewrite"] is False and body["mode"] == "facts" and body["budget"] == 300
        return httpx.Response(
            200,
            json={"text": "Known facts: ...", "memories": [{"id": 1}], "turns": [],
                  "as_of": None, "search_text": "where do I live"},
        )  # fmt: skip

    r = client(handler).search("where do I live", mode="facts", budget=300, rewrite=False)

    assert r.text.startswith("Known facts") and r.memories == [{"id": 1}] and r.as_of is None


def test_forget_with_ids_and_without():
    calls: list[tuple[str, str]] = []

    def handler(request):
        calls.append((request.method, str(request.url)))
        return httpx.Response(200, json={"memories": 1, "turns": 1})

    mem = client(handler)
    mem.forget([3, 4])
    mem.forget()

    assert calls[0] == ("DELETE", "http://t/memories?user_id=aman&ids=3&ids=4")
    assert calls[1] == ("DELETE", "http://t/users/aman")


def test_http_errors_raise():
    mem = client(lambda request: httpx.Response(401, json={"detail": "bad or missing X-API-Key"}))
    with pytest.raises(httpx.HTTPStatusError):
        mem.search("anything")
