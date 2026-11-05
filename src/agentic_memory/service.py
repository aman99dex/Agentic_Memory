"""One object that the REST API, the MCP server and the CLI all call (ADR 0014).

A save stores the turn and returns; the worker does the LLM work in the background (ADR 0020).
"""

import asyncio
import contextlib
import hashlib
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from agentic_memory import config
from agentic_memory.context import context
from agentic_memory.db import open_store
from agentic_memory.rank import Rank
from agentic_memory.read_path import Recall, ReturnMode, recall
from agentic_memory.store import Memory, Store
from agentic_memory.telemetry import init_telemetry
from agentic_memory.worker import Worker
from agentic_memory.write_path import Step, build_graph, make_llm

WAIT_SECONDS = 120  # how long `wait=True` waits for another worker to finish this user


@dataclass(frozen=True)
class Saved:
    turn_id: int
    new: bool  # False: this exact round was already stored, nothing was queued
    steps: list[Step] | None = None  # only with wait=True: what was done with each fact


@dataclass(frozen=True)
class NewTurn:
    session_id: str
    user_text: str
    assistant_text: str
    occurred_at: datetime


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class MemoryService:
    def __init__(
        self, store: Store, extract_llm: Any, query_llm: Any, rank: Rank | None = None
    ) -> None:
        self.store = store
        self._query_llm = query_llm
        self._rank = rank  # None: plain RRF order and recalls leave no trace (ADR 0016)
        self.worker = Worker(store, build_graph(store, extract_llm, extract_llm), extract_llm)

    async def ingest(
        self,
        user_id: str,
        session_id: str,
        user_text: str,
        assistant_text: str = "",
        occurred_at: datetime | None = None,
        *,
        namespace: str | None = None,
        wait: bool = False,
    ) -> Saved:
        """Store one round. Without `wait` it returns at once and the worker processes it.

        Idempotency keys on occurred_at (ADR 0013): a client that retries must send the same one.
        """
        when = occurred_at or datetime.now(UTC)
        turn_id, new = await self.store.add_turn(
            user_id, session_id, user_text, assistant_text, when, namespace
        )
        if not wait:
            self.worker.wake.set()
            return Saved(turn_id, new)
        return Saved(turn_id, new, await self._process_now(user_id, turn_id))

    async def _process_now(self, user_id: str, turn_id: int) -> list[Step]:
        """Drain this user's queue here. If another worker holds the lease, wait for it."""
        async with asyncio.timeout(WAIT_SECONDS):
            while not await self.store.turn_processed(user_id, turn_id):
                done = await self.worker.drain_user(user_id)
                if turn_id in done:
                    return done[turn_id]
                await asyncio.sleep(0.2)
        return []  # processed by another worker, or earlier: its steps aren't ours to report

    async def import_turns(
        self, user_id: str, turns: Sequence[NewTurn], namespace: str | None = None
    ) -> tuple[int, int]:
        """Queue a batch of past rounds (oldest first). Returns (stored, already there)."""
        stored = 0
        for t in sorted(turns, key=lambda t: t.occurred_at):
            _, new = await self.store.add_turn(
                user_id, t.session_id, t.user_text, t.assistant_text, t.occurred_at, namespace
            )
            stored += new
        self.worker.wake.set()
        return stored, len(turns) - stored

    async def recall(
        self,
        user_id: str,
        query: str,
        *,
        context: Sequence[str] = (),
        budget: int = 1500,
        mode: ReturnMode = "both",
        rewrite: bool = True,
        namespace: str | None = None,
    ) -> Recall:
        now = datetime.now(UTC)
        r = await recall(
            self.store,
            self._query_llm,
            user_id,
            query,
            now=now,
            context=context,
            budget=budget,
            mode=mode,
            rank=self._rank,
            rewrite=rewrite,
            namespace=namespace,
        )
        if self._rank:  # being recalled is what makes a fact important
            await self.store.touch(user_id, [m.id for m in r.memories], now, self._rank)
        return r

    async def context(self, user_id: str, namespace: str | None = None, budget: int = 1200) -> str:
        return await context(self.store, user_id, namespace, budget=budget)

    async def history(self, user_id: str, memory_id: int) -> list[Memory]:
        return await self.store.history(user_id, memory_id)

    async def forget(
        self, user_id: str, memory_ids: Sequence[int] | None = None
    ) -> tuple[int, int]:
        return await self.store.forget(user_id, memory_ids)

    async def tenant(self, api_key: str) -> str | None:
        return await self.store.tenant_for_key(key_hash(api_key))


@asynccontextmanager
async def open_service(
    url: str | None = None, *, run_worker: bool = True
) -> AsyncIterator[MemoryService]:
    """Configured from the environment: DATABASE_URL, *_MODEL and the provider's API key.

    With `run_worker`, a background task processes saved turns for as long as it is open.
    """
    config.load()
    init_telemetry()
    async with open_store(url) as store:
        service = MemoryService(
            store, make_llm("EXTRACT_MODEL"), make_llm("QUERY_MODEL"), Rank.from_env()
        )
        task = asyncio.create_task(service.worker.run_forever()) if run_worker else None
        try:
            yield service
        finally:
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
