"""pack() on its own, then recall() end to end with the real LLM (run with --env-file .env)."""

import os
import uuid
from datetime import UTC, datetime

import pytest
from pgvector.psycopg import register_vector_async
from psycopg_pool import AsyncConnectionPool

from agentic_memory.embedder import Embedder
from agentic_memory.read_path import pack, recall
from agentic_memory.store import Memory, MemoryStore, Turn
from agentic_memory.write_path import build_graph, ingest_turn, make_llm

JAN, MAR, OCT = (datetime(2026, m, 1, tzinfo=UTC) for m in (1, 3, 10))


def mem(i: int, text: str) -> Memory:
    return Memory(i, "u", text, [i], JAN, None, None, JAN)


def turn(i: int, text: str) -> Turn:
    return Turn(i, "s", text, "ok", JAN)


def test_pack_puts_facts_first_and_skips_what_does_not_fit():
    facts = [mem(1, "The user lives in Pune."), mem(2, "x" * 4000)]
    turns = [turn(1, "I moved to Pune")]
    out = pack(facts, turns, budget=100)
    assert out.startswith("Known facts:") and "Pune" in out
    assert "x" * 100 not in out  # the huge fact didn't fit
    assert "Source conversations:" in out  # the leftover budget still goes to the turns


def test_pack_with_room_includes_the_source_turn():
    out = pack([mem(1, "The user lives in Pune.")], [turn(1, "I moved to Pune")], budget=500)
    assert "Source conversations:" in out and "user: I moved to Pune" in out


def test_pack_nothing_fits_returns_empty():
    assert pack([mem(1, "y" * 4000)], [], budget=10) == ""


needs_llm = pytest.mark.skipif(
    "ANTHROPIC_API_KEY" not in os.environ, reason="needs a real LLM key (--env-file .env)"
)


@pytest.fixture(scope="session")
def embedder():
    return Embedder()


@pytest.fixture
async def store(db_url, embedder):
    async with AsyncConnectionPool(db_url, configure=register_vector_async, open=False) as pool:
        yield MemoryStore(pool, embedder)


@needs_llm
async def test_recall_returns_current_facts_with_their_turns_and_honours_the_past(store):
    user = f"user-{uuid.uuid4().hex[:8]}"
    llm = make_llm()
    graph = build_graph(store, llm, llm)
    await ingest_turn(store, graph, user, "s1", "I live in Bangalore.", "Nice city!", JAN)
    await ingest_turn(store, graph, user, "s2", "I just moved to Pune for a new job.", "Wow!", OCT)
    now = datetime(2026, 10, 26, tzinfo=UTC)

    current = await recall(store, llm, user, "Where does the user live?", now=now)
    assert "Pune" in current.text and "Bangalore" not in current.memories[0].content
    assert any("moved to Pune" in t.user_text for t in current.turns)  # the turn, with the reason

    past = await recall(store, llm, user, "Where did the user live in March 2026?", now=now)
    assert past.query.as_of is not None
    assert any("Bangalore" in m.content for m in past.memories)

    only_facts = await recall(store, llm, user, "Where does the user live?", now=now, mode="facts")
    assert only_facts.turns == [] and "Source conversations" not in only_facts.text
