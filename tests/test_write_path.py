"""The write path end to end: real Postgres, real embedder, real LLM (Haiku, ADR 0012).

Run with the key loaded:  uv run --env-file .env pytest tests/test_write_path.py
"""

import os
import uuid
from datetime import UTC, datetime

import pytest
from pgvector.psycopg import register_vector_async
from psycopg_pool import AsyncConnectionPool

from agentic_memory.embedder import Embedder
from agentic_memory.store import MemoryStore
from agentic_memory.write_path import build_graph, ingest_turn, make_llm

pytestmark = pytest.mark.skipif(
    "ANTHROPIC_API_KEY" not in os.environ, reason="needs a real LLM key (run with --env-file .env)"
)

JAN, MAR, OCT = (datetime(2026, m, 1, tzinfo=UTC) for m in (1, 3, 10))


@pytest.fixture(scope="session")
def embedder():
    return Embedder()


@pytest.fixture
async def store(db_url, embedder):
    async with AsyncConnectionPool(db_url, configure=register_vector_async, open=False) as pool:
        yield MemoryStore(pool, embedder)


@pytest.fixture
def graph(store):
    llm = make_llm()
    return build_graph(store, llm, llm)


@pytest.fixture
def user():
    return f"user-{uuid.uuid4().hex[:8]}"


async def count(store, user, where="TRUE"):
    async with store._pool.connection() as conn:
        cur = await conn.execute(
            f"SELECT count(*) FROM memories WHERE user_id = %s AND {where}",  # noqa: S608
            (user,),
        )
        return (await cur.fetchone())[0]


async def test_move_replaces_the_old_city_and_keeps_history(store, graph, user):
    await ingest_turn(
        store, graph, user, "s1", "I live in Bangalore and work at Neoflo.", "Nice!", JAN
    )
    await ingest_turn(store, graph, user, "s2", "Big news: I just moved to Pune.", "Congrats!", OCT)

    now = [m.content for m in await store.search(user, "which city does the user live in")]
    assert any("Pune" in c for c in now) and not any("Bangalore" in c for c in now)
    then = [
        m.content for m in await store.search(user, "which city does the user live in", as_of=MAR)
    ]
    assert any("Bangalore" in c for c in then) and not any("Pune" in c for c in then)
    assert await count(store, user, "superseded_by IS NOT NULL") == 1


async def test_same_turn_twice_and_chit_chat_add_nothing(store, graph, user):
    args = (user, "s1", "I'm allergic to peanuts.", "Noted.", JAN)
    assert await ingest_turn(store, graph, *args)
    stored = await count(store, user)
    assert stored >= 1

    assert await ingest_turn(store, graph, *args) == []  # exact repeat: skipped
    await ingest_turn(store, graph, user, "s1", "ok thanks", "You're welcome!", MAR)
    assert await count(store, user) == stored
