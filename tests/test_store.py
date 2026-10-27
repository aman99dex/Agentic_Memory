"""MemoryStore against a real Postgres and the real embedding model."""

import uuid
from datetime import UTC, datetime

import pytest
from pgvector.psycopg import register_vector_async
from psycopg_pool import AsyncConnectionPool

from agentic_memory.embedder import Embedder
from agentic_memory.store import MemoryStore

JAN, MAR, OCT = (datetime(2026, m, 1, tzinfo=UTC) for m in (1, 3, 10))


@pytest.fixture(scope="session")
def embedder():
    return Embedder()


@pytest.fixture
async def store(db_url, embedder):
    async with AsyncConnectionPool(db_url, configure=register_vector_async, open=False) as pool:
        yield MemoryStore(pool, embedder)


@pytest.fixture
def user():
    return f"user-{uuid.uuid4().hex[:8]}"  # every test gets its own tenant


async def test_get_is_scoped_to_the_owner(store, user):
    mid = await store.add(user, "Aman lives in Pune", [1], OCT)
    assert (await store.get(user, mid)).content == "Aman lives in Pune"
    assert await store.get("someone-else", mid) is None


async def test_supersede_keeps_history(store, user):
    old = await store.add(user, "Aman lives in Bangalore", [1], JAN)
    new = await store.supersede(user, old, "Aman lives in Pune", [2], OCT)

    closed = await store.get(user, old)
    assert closed.valid_to == OCT and closed.superseded_by == new
    assert [m.content for m in await store.search(user, "where does Aman live")] == [
        "Aman lives in Pune"
    ]
    assert [m.content for m in await store.search(user, "where does Aman live", as_of=MAR)] == [
        "Aman lives in Bangalore"
    ]


async def test_failed_supersede_leaves_no_half_write(store, user):
    with pytest.raises(LookupError):
        await store.supersede(user, 999_999, "Aman lives in Mumbai", [3], OCT)
    assert await store.search_text(user, "Mumbai") == []  # the insert was rolled back


async def test_invalidate_only_closes_current_facts(store, user):
    mid = await store.add(user, "Aman has a dog", [1], JAN)
    assert await store.invalidate(user, mid, OCT) is True
    assert await store.invalidate(user, mid, OCT) is False  # already closed
    assert await store.search(user, "dog") == []


async def test_hybrid_fixes_what_vector_search_gets_wrong(store, user):
    ids = {}
    for n, thing in (("7731", "chair"), ("7732", "table"), ("7730", "lamp")):
        ids[n] = await store.add(user, f"Order A-{n} was a refund for the blue {thing}", [1], OCT)
    await store.add(user, "Aman lives in Pune", [2], OCT)

    question = "what was order A-7731 about?"
    assert (await store.search_text(user, question))[0] == ids["7731"]
    assert (await store.search(user, question))[0].id == ids["7731"]


async def test_keyword_side_matches_any_word(store, user):
    mid = await store.add(user, "Aman lives in Pune", [1], OCT)
    # 'city' is not in the fact; with AND-semantics this found nothing (Lesson 1.7)
    assert await store.search_text(user, "which city do I live in?") == [mid]


async def test_add_turn_is_idempotent(store, user):
    first = await store.add_turn(user, "s1", "I moved to Pune", "Nice!", OCT)
    again = await store.add_turn(user, "s1", "I moved to Pune", "Nice!", OCT)
    other_session = await store.add_turn(user, "s2", "I moved to Pune", "Nice!", OCT)

    assert first[1] is True and again == (first[0], False)  # same round: stored once
    assert other_session[1] is True  # same words in another session is a different round


async def test_history_lists_every_version_from_any_of_them(store, user):
    a = await store.add(user, "Aman lives in Bangalore", [1], JAN)
    b = await store.supersede(user, a, "Aman lives in Pune", [2], MAR)
    c = await store.supersede(user, b, "Aman lives in Mumbai", [3], OCT)
    for start in (a, b, c):
        assert [m.id for m in await store.history(user, start)] == [a, b, c]
    assert await store.history("someone-else", a) == []


async def test_forget_removes_the_chain_its_turns_and_siblings_and_only_those(store, user):
    t1, _ = await store.add_turn(user, "s", "I live in Bangalore, my phone is 555", "ok", JAN)
    t2, _ = await store.add_turn(user, "s", "I moved to Pune", "ok", OCT)
    t3, _ = await store.add_turn(user, "s", "I like tea", "ok", OCT)
    old = await store.add(user, "Aman lives in Bangalore", [t1], JAN)
    sibling = await store.add(user, "Aman's phone is 555", [t1], JAN)  # same turn as `old`
    new = await store.supersede(user, old, "Aman lives in Pune", [t2], OCT)
    tea = await store.add(user, "Aman likes tea", [t3], OCT)

    assert await store.forget(user, [new]) == (3, 2)  # old, sibling, new; turns t1, t2

    assert [await store.get(user, i) for i in (old, sibling, new)] == [None, None, None]
    assert (await store.get(user, tea)).content == "Aman likes tea"
    assert [t.id for t in await store.get_turns(user, [t1, t2, t3])] == [t3]


async def test_forget_everything_for_one_user_leaves_others_alone(store, user):
    other = f"{user}-other"
    t, _ = await store.add_turn(user, "s", "hello", "hi", JAN)
    await store.add(user, "fact", [t], JAN)
    keep = await store.add(other, "keep me", [1], JAN)

    assert await store.forget(user) == (1, 1)
    assert (await store.get(other, keep)).content == "keep me"
