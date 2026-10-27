"""One object that both the REST API and the MCP server call (ADR 0014)."""

import os
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from pgvector.psycopg import register_vector_async
from psycopg_pool import AsyncConnectionPool

from agentic_memory.embedder import Embedder
from agentic_memory.read_path import Recall, ReturnMode, recall
from agentic_memory.store import Memory, MemoryStore
from agentic_memory.write_path import Step, build_graph, ingest_turn, make_llm


class MemoryService:
    def __init__(self, store: MemoryStore, extract_llm: Any, query_llm: Any) -> None:
        self.store = store
        self._query_llm = query_llm
        self._graph = build_graph(store, extract_llm, extract_llm)

    async def ingest(
        self,
        user_id: str,
        session_id: str,
        user_text: str,
        assistant_text: str = "",
        occurred_at: datetime | None = None,
    ) -> list[Step]:
        # Idempotency keys on occurred_at (ADR 0013): a client that retries must send the same one.
        when = occurred_at or datetime.now(UTC)
        return await ingest_turn(
            self.store, self._graph, user_id, session_id, user_text, assistant_text, when
        )

    async def recall(
        self,
        user_id: str,
        query: str,
        *,
        context: Sequence[str] = (),
        budget: int = 1500,
        mode: ReturnMode = "both",
    ) -> Recall:
        return await recall(
            self.store,
            self._query_llm,
            user_id,
            query,
            now=datetime.now(UTC),
            context=context,
            budget=budget,
            mode=mode,
        )

    async def history(self, user_id: str, memory_id: int) -> list[Memory]:
        return await self.store.history(user_id, memory_id)

    async def forget(
        self, user_id: str, memory_ids: Sequence[int] | None = None
    ) -> tuple[int, int]:
        return await self.store.forget(user_id, memory_ids)


@asynccontextmanager
async def open_service() -> AsyncIterator[MemoryService]:
    """Everything is configured from the environment: DATABASE_URL, *_MODEL, ANTHROPIC_API_KEY."""
    async with AsyncConnectionPool(
        os.environ["DATABASE_URL"], configure=register_vector_async, open=False
    ) as pool:
        yield MemoryService(
            MemoryStore(pool, Embedder()), make_llm("EXTRACT_MODEL"), make_llm("QUERY_MODEL")
        )
