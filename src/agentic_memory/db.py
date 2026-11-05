"""Open the configured store (ADR 0019): Postgres for a postgresql:// URL, otherwise SQLite.

Kept apart from service.py so that opening a store never imports LangChain: the
session-start hook only reads, and it has to be quick.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from agentic_memory import config
from agentic_memory.embedder import Embedder
from agentic_memory.store import Store


@asynccontextmanager
async def open_store(url: str | None = None) -> AsyncIterator[Store]:
    url = url or config.database_url()
    if url.startswith(("postgresql://", "postgres://")):
        from pgvector.psycopg import register_vector_async  # noqa: PLC0415
        from psycopg_pool import AsyncConnectionPool  # noqa: PLC0415

        from agentic_memory.postgres_store import PostgresStore  # noqa: PLC0415

        async with AsyncConnectionPool(url, configure=register_vector_async, open=False) as pool:
            yield PostgresStore(pool, Embedder())
    else:
        from agentic_memory.sqlite_store import SqliteStore  # noqa: PLC0415

        store = SqliteStore(url.removeprefix("sqlite:///"), Embedder())
        try:
            yield store
        finally:
            store.close()
