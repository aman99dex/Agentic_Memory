"""The worker's rules with a scripted model (no network), on both databases (ADRs 0020-0021).

The real model runs in tests/test_local_mode.py; here we check what our code does with
whatever the model says: order, failures, leases, summaries, profiles.
"""

import re
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from agentic_memory.service import MemoryService
from agentic_memory.worker import Worker
from agentic_memory.write_path import Decision, Fact, Facts, build_graph

JAN = datetime(2026, 1, 1, tzinfo=UTC)
REPO = "c:/code/shop"


class Scripted:
    """Extraction: one fact per turn, "The user said: <text>"; "boom" fails, "ok" has none,
    "project" makes it a project fact. Reconcile: always ADD. Text: a fixed summary."""

    def __init__(self, summary="Did things."):
        self.summary = summary
        self.prompts: list[str] = []

    def with_structured_output(self, schema):
        return SimpleNamespace(ainvoke=lambda prompt: self._structured(schema, prompt))

    async def _structured(self, schema, prompt):
        if schema is Decision:
            return Decision(action="ADD")
        said = re.search(r"^USER: (.*)$", prompt, re.M).group(1)
        if "boom" in said:
            raise RuntimeError("model unavailable")
        if said == "ok":
            return Facts(facts=[])
        scope = "project" if "project" in said else "user"
        return Facts(facts=[Fact(text=f"The user said: {said}", scope=scope)])

    async def ainvoke(self, prompt):
        self.prompts.append(prompt)
        return SimpleNamespace(content=self.summary)


@pytest.fixture
def user():
    return f"user-{uuid.uuid4().hex[:8]}"


def worker(store, llm):
    return Worker(store, build_graph(store, llm, llm), llm)


async def test_drain_processes_oldest_first_and_survives_a_failing_turn(any_store, user):
    store, llm = any_store, Scripted()
    late, _ = await store.add_turn(user, "s1", "I like tea", "", JAN + timedelta(hours=2), REPO)
    bad, _ = await store.add_turn(user, "s1", "boom", "", JAN + timedelta(hours=1), REPO)
    early, _ = await store.add_turn(user, "s1", "the project uses Rails", "", JAN, REPO)

    done = await worker(store, llm).drain_user(user)

    assert list(done) == [early, late]  # time order, and the failure didn't stop the rest
    assert (await store.stats(user))["queued"] == 1  # the failed turn waits for a retry
    project = await store.current_facts(user, REPO)
    assert [m.content for m in project] == ["The user said: the project uses Rails"]
    assert [m.content for m in await store.current_facts(user, None)] == [
        "The user said: I like tea"
    ]


async def test_drain_writes_the_session_summary_and_both_profiles(any_store, user):
    store, llm = any_store, Scripted("Set the project up with Rails.")
    await store.add_turn(user, "s1", "the project uses Rails", "", JAN, REPO)
    await store.add_turn(user, "s1", "I like tea", "", JAN + timedelta(minutes=5), REPO)

    await worker(store, llm).drain_user(user)

    episode = await store.derived(user, "episode", session_id="s1")
    assert episode.content == "Set the project up with Rails." and episode.namespace == REPO
    assert len(episode.source_turn_ids) == 2 and episode.valid_from == JAN
    assert await store.derived(user, "profile") is not None  # a user fact changed
    assert await store.derived(user, "profile", REPO) is not None  # a project fact changed
    profile_prompts = [p for p in llm.prompts if "Facts:" in p]
    assert any("I like tea" in p for p in profile_prompts)


async def test_a_session_summary_only_reads_new_turns(any_store, user):
    store, llm = any_store, Scripted("Summary.")
    await store.add_turn(user, "s1", "first thing", "", JAN)
    w = worker(store, llm)
    await w.drain_user(user)
    await store.add_turn(user, "s1", "second thing", "", JAN + timedelta(hours=1))
    await w.drain_user(user)

    last = [p for p in llm.prompts if "New turns:" in p][-1]
    assert "second thing" in last and "first thing" not in last
    assert len((await store.derived(user, "episode", session_id="s1")).source_turn_ids) == 2


async def test_a_session_with_nothing_worth_keeping_gets_no_summary(any_store, user):
    store = any_store
    await store.add_turn(user, "s1", "ok", "", JAN)
    await worker(store, Scripted("NONE")).drain_user(user)
    assert await store.derived(user, "episode", session_id="s1") is None
    assert await store.derived(user, "profile") is None  # no fact changed


async def test_a_held_lease_makes_another_worker_back_off(any_store, user):
    store = any_store
    await store.add_turn(user, "s1", "I like tea", "", JAN)
    assert await store.claim(user, "someone-else", datetime.now(UTC), 60)
    assert await worker(store, Scripted()).drain_user(user) == {}
    assert (await store.stats(user))["queued"] == 1


async def test_ingest_queues_by_default_and_waits_when_asked(any_store, user):
    service = MemoryService(any_store, Scripted(), Scripted())
    saved = await service.ingest(user, "s1", "I like tea", occurred_at=JAN)
    assert saved.new and saved.steps is None
    assert (await any_store.stats(user))["queued"] == 1

    waited = await service.ingest(user, "s1", "I like coffee", occurred_at=JAN, wait=True)
    assert [s["fact"] for s in waited.steps] == ["The user said: I like coffee"]
    assert (await any_store.stats(user))["queued"] == 0  # the earlier turn went too

    again = await service.ingest(user, "s1", "I like coffee", occurred_at=JAN, wait=True)
    assert not again.new and again.steps == []  # a retry does nothing twice
