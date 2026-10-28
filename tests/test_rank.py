"""Rerank and nightly decay (ADR 0016): the formula on its own, then against real Postgres."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pgvector.psycopg import register_vector_async
from psycopg_pool import AsyncConnectionPool

from agentic_memory.decay import decay
from agentic_memory.embedder import Embedder
from agentic_memory.rank import Rank
from agentic_memory.service import MemoryService
from agentic_memory.store import MemoryStore

NOW = datetime(2026, 10, 1, tzinfo=UTC)
LONG_AGO = NOW - timedelta(days=730)


def test_recency_halves_every_half_life():
    r = Rank(importance_weight=0.0, half_life_days=100)
    assert r.factor(0.0, 0) == 1.0
    assert r.factor(0.0, 100) == pytest.approx(0.5)
    assert r.factor(0.0, 200) == pytest.approx(0.25)


def test_importance_boosts_and_off_is_neutral():
    assert Rank(importance_weight=0.5, half_life_days=0).factor(1.0, 999) == pytest.approx(1.5)
    assert Rank.off().factor(1.0, 999) == 1.0


def test_a_future_timestamp_does_not_boost_above_one():
    assert Rank(importance_weight=0.0, half_life_days=100).factor(0.0, -50) == 1.0


@pytest.fixture(scope="session")
def embedder():
    return Embedder()


@pytest.fixture
async def store(db_url, embedder):
    async with AsyncConnectionPool(db_url, configure=register_vector_async, open=False) as pool:
        yield MemoryStore(pool, embedder)


@pytest.fixture
def user():
    return f"user-{uuid.uuid4().hex[:8]}"


async def test_a_recent_fact_outranks_an_identical_old_one(store, user):
    old = await store.add(user, "The user drinks green tea.", [1], LONG_AGO)
    new = await store.add(user, "The user drinks green tea.", [2], NOW - timedelta(days=1))

    found = await store.search(user, "green tea", rank=Rank(), now=NOW)

    assert [m.id for m in found] == [new, old]


async def test_recalling_a_fact_makes_it_outrank_an_untouched_twin(store, user):
    first = await store.add(user, "The user plays chess.", [1], LONG_AGO)
    second = await store.add(user, "The user plays chess.", [2], LONG_AGO)
    rank = Rank()

    await store.touch(user, [second], NOW, rank)

    assert [m.id for m in await store.search(user, "chess", rank=rank, now=NOW)] == [second, first]


class NoLLM:
    def with_structured_output(self, _schema):
        return self


async def test_service_recall_makes_a_fact_more_important_and_needs_no_llm(store, user, conn):
    mid = await store.add(user, "The user speaks Marathi.", [1], LONG_AGO)
    service = MemoryService(store, NoLLM(), NoLLM(), Rank())

    first = await service.recall(user, "Marathi", rewrite=False)
    await service.recall(user, "Marathi", rewrite=False)

    assert [m.id for m in first.memories] == [mid]
    row = conn.execute(
        "SELECT importance, last_accessed_at FROM memories WHERE id = %s", (mid,)
    ).fetchone()
    assert row[0] == pytest.approx(0.2) and row[1] is not None  # two recalls, +0.1 each


async def test_without_rank_both_facts_still_come_back(store, user):
    await store.add(user, "The user owns a bike.", [1], LONG_AGO)
    await store.add(user, "The user owns a bike.", [2], NOW)
    assert len(await store.search(user, "bike")) == 2


async def test_decay_archives_cold_facts_and_keeps_them_recoverable(store, user, conn):
    today = datetime.now(UTC)
    cold = await store.add(user, "The user collects stamps.", [1], LONG_AGO)
    warm = await store.add(user, "The user collects coins.", [2], LONG_AGO)
    await store.touch(user, [warm], today, Rank(access_boost=0.5))  # recalled: importance 0.5

    # created_at is the real clock, so "a year and a bit later" is what makes a fact cold.
    decayed, archived = decay(conn, today + timedelta(days=400))

    assert decayed >= 1 and archived >= 1
    assert await store.search_text(user, "stamps") == []  # hidden from search...
    assert [m.id for m in await store.history(user, cold)] == [cold]  # ...but not deleted
    assert await store.search_text(user, "coins") == [warm]  # importance 0.45 > floor: kept
    assert await store.unarchive(user, [cold]) == 1
    assert await store.search_text(user, "stamps") == [cold]


async def test_decay_leaves_closed_facts_alone(store, user, conn):
    mid = await store.add(user, "The user has a cat.", [1], LONG_AGO)
    await store.invalidate(user, mid, NOW - timedelta(days=400))

    decay(conn, datetime.now(UTC) + timedelta(days=400))

    row = conn.execute("SELECT archived_at FROM memories WHERE id = %s", (mid,)).fetchone()
    assert row == (None,)  # closed facts are history already; archiving them would hide nothing
