"""REST API through the real ASGI app. Auth and validation need no LLM; the round trip does."""

import os
import uuid
from datetime import UTC, datetime

import httpx
import pytest
from pgvector.psycopg import register_vector_async
from psycopg_pool import AsyncConnectionPool

from agentic_memory.api import create_app
from agentic_memory.embedder import Embedder
from agentic_memory.postgres_store import MemoryStore
from agentic_memory.service import MemoryService, key_hash
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


async def test_search_with_rewrite_false_finds_facts_without_any_llm(store):
    user = f"user-{uuid.uuid4().hex[:8]}"
    await store.add(user, "The user lives in Pune.", [1], datetime(2026, 1, 1, tzinfo=UTC))
    async with await client(MemoryService(store, NoLLM(), NoLLM())) as c:
        r = await c.post(
            "/memories:search",
            json={"user_id": user, "query": "Pune", "rewrite": False, "mode": "facts"},
            headers=KEY,
        )
    assert r.status_code == 200
    assert "lives in Pune" in r.json()["text"] and r.json()["search_text"] == "Pune"


async def test_tenant_keys_see_only_their_own_users(store):
    service = MemoryService(store, NoLLM(), NoLLM())
    a, b = (f"t{uuid.uuid4().hex[:6]}" for _ in range(2))
    await store.add_api_key(a, key_hash("key-a"))
    await store.add_api_key(b, key_hash("key-b"))
    when = datetime(2026, 1, 1, tzinfo=UTC)
    await store.add(f"{a}:bob", "Bob lives in Pune.", [1], when)  # tenant a's user "bob"

    async with await client(service) as c:

        def search(key):
            body = {"user_id": "bob", "query": "Pune", "rewrite": False, "mode": "facts"}
            return c.post("/memories:search", json=body, headers={"X-API-Key": key})

        seen_by_a = (await search("key-a")).json()
        assert "Pune" in seen_by_a["text"]
        assert seen_by_a["memories"][0]["user_id"] == "bob"  # never the stored "a:bob"
        assert (await search("key-b")).json()["memories"] == []  # same name, other tenant
        assert (await search("test-key")).json()["memories"] == []  # the operator's "bob"
        await store.revoke_api_keys(a, when)
        assert (await search("key-a")).status_code == 401


async def test_import_and_context_need_no_llm(store):
    user = f"user-{uuid.uuid4().hex[:8]}"
    service = MemoryService(store, NoLLM(), NoLLM())
    turns = [
        {"session_id": "s1", "user_text": "later", "occurred_at": "2026-02-01T00:00:00"},
        {"session_id": "s1", "user_text": "earlier", "occurred_at": "2026-01-01T00:00:00Z"},
    ]
    async with await client(service) as c:
        r = await c.post("/memories:import", json={"user_id": user, "turns": turns}, headers=KEY)
        assert r.json() == {"stored": 2, "duplicates": 0}
        r = await c.post("/memories:import", json={"user_id": user, "turns": turns}, headers=KEY)
        assert r.json() == {"stored": 0, "duplicates": 2}  # a re-import is a no-op
        assert [t.user_text for t in await store.pending_turns(user)] == ["earlier", "later"]

        await store.add(user, "The user lives in Pune.", [1], datetime(2026, 1, 1, tzinfo=UTC))
        r = await c.post("/memories:context", json={"user_id": user}, headers=KEY)
        assert "About the user:\n- The user lives in Pune." in r.json()["text"]


@needs_llm
async def test_ingest_search_history_forget_round_trip(store):
    llm = make_llm()
    user = f"user-{uuid.uuid4().hex[:8]}"
    async with await client(MemoryService(store, llm, llm)) as c:

        async def ingest(sid, text, when):
            body = {"user_id": user, "session_id": sid, "user_text": text, "occurred_at": when}
            r = await c.post("/memories:ingest", headers=KEY, json=body | {"wait": True})
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
