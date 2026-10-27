"""REST API through the real ASGI app. Auth and validation need no LLM; the round trip does."""

import os
import uuid

import httpx
import pytest
from pgvector.psycopg import register_vector_async
from psycopg_pool import AsyncConnectionPool

from agentic_memory.api import create_app
from agentic_memory.embedder import Embedder
from agentic_memory.service import MemoryService
from agentic_memory.store import MemoryStore
from agentic_memory.write_path import make_llm

KEY = {"X-API-Key": "test-key"}
needs_llm = pytest.mark.skipif(
    "ANTHROPIC_API_KEY" not in os.environ, reason="needs a real LLM key (--env-file .env)"
)


class NoLLM:
    """Stands in where a request is rejected before any model call would happen."""

    def with_structured_output(self, _schema):
        return self


@pytest.fixture(scope="session")
def embedder():
    return Embedder()


@pytest.fixture
async def store(db_url, embedder):
    async with AsyncConnectionPool(db_url, configure=register_vector_async, open=False) as pool:
        yield MemoryStore(pool, embedder)


async def client(service):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(service, "test-key")), base_url="http://t"
    )


async def test_every_route_needs_the_key(store):
    async with await client(MemoryService(store, NoLLM(), NoLLM())) as c:
        body = {"user_id": "u", "query": "q"}
        assert (await c.post("/memories:search", json=body)).status_code == 401
        assert (
            await c.post("/memories:search", json=body, headers={"X-API-Key": "no"})
        ).status_code == 401
        assert (await c.delete("/users/u")).status_code == 401


async def test_bad_input_is_rejected_at_the_boundary(store):
    async with await client(MemoryService(store, NoLLM(), NoLLM())) as c:
        post = lambda path, **j: c.post(path, json=j, headers=KEY)  # noqa: E731
        assert (
            await post("/memories:ingest", user_id="", session_id="s", user_text="x")
        ).status_code == 422
        assert (
            await post("/memories:ingest", user_id="u", session_id="s", user_text="x" * 20_001)
        ).status_code == 422
        assert (
            await post("/memories:search", user_id="u", query="q", budget=10**9)
        ).status_code == 422
        assert (await c.delete("/memories?user_id=u", headers=KEY)).status_code == 422  # no ids


def test_the_api_refuses_to_start_without_a_key(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        create_app(None, None)


@needs_llm
async def test_ingest_search_history_forget_round_trip(store):
    llm = make_llm()
    user = f"user-{uuid.uuid4().hex[:8]}"
    async with await client(MemoryService(store, llm, llm)) as c:

        async def ingest(sid, text, when):
            r = await c.post(
                "/memories:ingest",
                headers=KEY,
                json={"user_id": user, "session_id": sid, "user_text": text, "occurred_at": when},
            )
            assert r.status_code == 200, r.text
            return r.json()["steps"]

        assert await ingest("s1", "I live in Bangalore.", "2026-01-05T10:00:00")
        assert await ingest("s2", "I moved to Pune.", "2026-10-05T10:00:00")
        assert await ingest("s2", "I moved to Pune.", "2026-10-05T10:00:00") == []  # retry: no-op

        found = (
            await c.post(
                "/memories:search", headers=KEY, json={"user_id": user, "query": "Where do I live?"}
            )
        ).json()
        assert "Pune" in found["text"] and "Bangalore" not in found["text"].split("Source")[0]
        pune = next(m for m in found["memories"] if "Pune" in m["content"])

        versions = (
            await c.get(f"/memories/{pune['id']}/history", params={"user_id": user}, headers=KEY)
        ).json()
        assert [("Bangalore" in v["content"]) for v in versions] == [True, False]
        assert (
            await c.get(f"/memories/{pune['id']}/history", params={"user_id": "x"}, headers=KEY)
        ).status_code == 404

        gone = (
            await c.delete("/memories", params={"user_id": user, "ids": [pune["id"]]}, headers=KEY)
        ).json()
        assert gone["memories"] >= 2 and gone["turns"] >= 2
        after = (
            await c.post(
                "/memories:search", headers=KEY, json={"user_id": user, "query": "Where do I live?"}
            )
        ).json()
        assert after["memories"] == []
