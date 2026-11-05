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
        return httpx.Response(200, json={"turn_id": 7, "queued": True})

    saved = client(handler).add("I moved to Pune.", occurred_at=WHEN)

    assert saved == {"turn_id": 7, "queued": True}
    (request,) = seen
    assert request.url.path == "/memories:ingest"
    assert request.headers["x-api-key"] == "secret"
    body = json.loads(request.content)
    assert body["user_id"] == "aman" and body["user_text"] == "I moved to Pune."
    assert body["occurred_at"] == WHEN.isoformat()  # a retry with the same value is idempotent
    assert body["wait"] is False and body["namespace"] is None  # background by default


def test_namespace_rides_on_every_call_and_context_returns_text():
    bodies: list[dict] = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"text": "About the user: ..."})

    mem = Memory("aman", "http://t", "k", namespace="shop", transport=httpx.MockTransport(handler))
    assert mem.context(budget=300) == "About the user: ..."
    assert bodies == [{"user_id": "aman", "namespace": "shop", "budget": 300}]


def test_import_sends_iso_times_in_chunks_of_1000():
    sizes: list[int] = []

    def handler(request):
        body = json.loads(request.content)
        sizes.append(len(body["turns"]))
        assert body["turns"][0]["occurred_at"] == WHEN.isoformat()
        return httpx.Response(200, json={"stored": len(body["turns"]), "duplicates": 0})

    rows = [{"session_id": "s", "user_text": f"t{i}", "occurred_at": WHEN} for i in range(1500)]
    assert client(handler).import_turns(rows) == {"stored": 1500, "duplicates": 0}
    assert sizes == [1000, 500]


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
