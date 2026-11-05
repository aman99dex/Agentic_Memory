"""Phase 7 storage rules, on both databases: the queue and leases (ADR 0020), scopes,
episodes and profiles (ADR 0021), tenant keys (ADR 0023)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from agentic_memory.sqlite_store import SqliteStore
from agentic_memory.store import MAX_ATTEMPTS

JAN, MAR, OCT = (datetime(2026, m, 1, tzinfo=UTC) for m in (1, 3, 10))
REPO_A, REPO_B = "c:/code/a", "c:/code/b"


@pytest.fixture
def store(any_store):
    return any_store


@pytest.fixture
def user():
    return f"user-{uuid.uuid4().hex[:8]}"


async def test_pending_turns_come_oldest_first_and_leave_when_finished(store, user):
    late, _ = await store.add_turn(user, "s", "second", "", OCT)
    early, _ = await store.add_turn(user, "s", "first", "", JAN, REPO_A)

    pending = await store.pending_turns(user)
    assert [t.id for t in pending] == [early, late]
    assert pending[0].namespace == REPO_A
    assert user in await store.pending_users()

    await store.finish_turn(early, OCT)
    assert [t.id for t in await store.pending_turns(user)] == [late]
    assert await store.turn_processed(user, early) and not await store.turn_processed(user, late)


async def test_a_failing_turn_stops_after_max_attempts(store, user):
    tid, _ = await store.add_turn(user, "s", "boom", "", JAN)
    for _ in range(MAX_ATTEMPTS):
        assert await store.pending_turns(user)
        await store.finish_turn(tid, JAN, "RateLimitError: slow down")
    assert await store.pending_turns(user) == []
    stats = await store.stats(user)
    assert stats["failed"] == 1 and stats["queued"] == 0
    assert stats["last error"] == "RateLimitError: slow down"


async def test_one_lease_per_user_until_it_expires_or_is_released(store, user):
    assert await store.claim(user, "a", JAN, 60)
    assert not await store.claim(user, "b", JAN, 60)  # a holds it
    assert await store.claim(user, "a", JAN, 60)  # the holder renews
    assert await store.claim(user, "b", JAN + timedelta(seconds=61), 60)  # a's expired
    await store.release(user, "a")  # not a's any more: no effect
    assert not await store.claim(user, "a", JAN + timedelta(seconds=62), 60)
    await store.release(user, "b")
    assert await store.claim(user, "a", JAN + timedelta(seconds=62), 60)
    assert await store.claim(f"{user}-2", "b", JAN, 60)  # leases are per user


async def test_user_wide_facts_show_everywhere_project_facts_only_at_home(store, user):
    everywhere = await store.add(user, "The user writes Python with uv.", [1], JAN)
    in_a = await store.add(user, "The project deploys to Cloud Run.", [2], JAN, REPO_A)
    in_b = await store.add(user, "The project deploys to Fly.io.", [3], JAN, REPO_B)

    def ids(found):
        return {m.id for m in found}

    q = "where does it deploy and which tools"
    assert ids(await store.search(user, q, namespace=REPO_A)) == {everywhere, in_a}
    assert ids(await store.search(user, q, namespace=REPO_B)) == {everywhere, in_b}
    assert ids(await store.search(user, q)) == {everywhere, in_a, in_b}  # no scope: all of it
    assert [m.id for m in await store.current_facts(user, None)] == [everywhere]
    assert [m.id for m in await store.current_facts(user, REPO_A)] == [in_a]


async def test_a_fact_keeps_its_namespace_when_superseded(store, user):
    old = await store.add(user, "The project deploys to Neon.", [1], JAN, REPO_A)
    new = await store.supersede(user, old, "The project uses Supabase.", [2], OCT, REPO_A)
    assert (await store.get(user, new)).namespace == REPO_A
    assert await store.search(user, "Supabase", namespace=REPO_B) == []


async def test_a_late_older_turn_closes_the_fact_at_its_start_instead_of_failing(store, user):
    old = await store.add(user, "The user lives in Pune.", [1], OCT)
    new = await store.supersede(user, old, "The user lives in Delhi.", [2], MAR)  # older!
    closed = await store.get(user, old)
    assert closed.valid_to == OCT and closed.superseded_by == new


async def test_episodes_and_profiles_are_rewritten_in_place(store, user):
    first = await store.put_derived(user, "episode", "Set up uv.", [1], JAN, REPO_A, "s1")
    again = await store.put_derived(
        user, "episode", "Set up uv; fixed CI.", [1, 2], JAN, REPO_A, "s1"
    )
    other = await store.put_derived(user, "episode", "Wrote docs.", [3], MAR, REPO_A, "s2")
    assert first == again != other
    episode = await store.derived(user, "episode", session_id="s1")
    assert episode.content == "Set up uv; fixed CI." and episode.source_turn_ids == [1, 2]
    assert [m.id for m in await store.recent_episodes(user, REPO_A)] == [other, first]
    assert await store.recent_episodes(user, REPO_B) == []

    me = await store.put_derived(user, "profile", "- Uses uv", [], JAN)
    project = await store.put_derived(user, "profile", "- Cloud Run", [], JAN, REPO_A)
    assert me != project
    assert await store.put_derived(user, "profile", "- Uses uv and ruff", [], MAR) == me
    assert (await store.derived(user, "profile")).content == "- Uses uv and ruff"
    assert (await store.derived(user, "profile", REPO_A)).content == "- Cloud Run"


async def test_episodes_are_searchable_but_profiles_are_not(store, user):
    await store.put_derived(user, "episode", "Migrated the billing service to Kafka.", [1], JAN)
    await store.put_derived(user, "profile", "- Works on billing with Kafka", [], JAN)
    found = await store.search(user, "billing Kafka")
    assert [m.kind for m in found] == ["episode"]
    assert await store.search(user, "billing Kafka", kinds=("fact",)) == []


async def test_forget_drops_profiles_and_episodes_built_from_the_forgotten(store, user):
    t1, _ = await store.add_turn(user, "s1", "my phone is 555", "ok", JAN)
    fact = await store.add(user, "The user's phone is 555.", [t1], JAN)
    await store.put_derived(user, "episode", "User shared their phone.", [t1], JAN, None, "s1")
    await store.put_derived(user, "profile", "- Phone 555", [], JAN)

    assert await store.forget(user, [fact]) == (2, 1)  # the fact and the episode; the turn
    assert await store.derived(user, "episode", session_id="s1") is None
    assert await store.derived(user, "profile") is None  # rebuilt later from what remains


async def test_tenant_keys_resolve_until_revoked(store):
    tenant = f"t-{uuid.uuid4().hex[:6]}"
    await store.add_api_key(tenant, "hash-1")
    assert await store.tenant_for_key("hash-1") == tenant
    assert await store.tenant_for_key("hash-2") is None
    assert await store.revoke_api_keys(tenant, OCT) == 1
    assert await store.tenant_for_key("hash-1") is None


async def test_sqlite_reopens_and_two_connections_share_one_file(tmp_path, shared_embedder):
    path = tmp_path / "m.db"
    a = SqliteStore(path, shared_embedder)
    b = SqliteStore(path, shared_embedder)  # migrations already applied: a no-op
    try:
        tid, new = await a.add_turn("u", "s", "hello", "", JAN)
        assert new and await b.add_turn("u", "s", "hello", "", JAN) == (tid, False)
        assert await a.claim("u", "a", JAN, 60) and not await b.claim("u", "b", JAN, 60)
    finally:
        a.close()
        b.close()
