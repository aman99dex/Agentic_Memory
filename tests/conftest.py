import asyncio
import sys
from collections.abc import AsyncIterator, Iterator

import psycopg
import pytest
from pgvector.psycopg import register_vector, register_vector_async
from psycopg_pool import AsyncConnectionPool
from testcontainers.community.postgres import PostgresContainer

from agentic_memory.embedder import Embedder
from agentic_memory.migrate import migrate
from agentic_memory.postgres_store import PostgresStore
from agentic_memory.sqlite_store import SqliteStore
from agentic_memory.store import Store

IMAGE = "pgvector/pgvector:0.8.7-pg18"


def pytest_asyncio_loop_factories(config, item):
    # psycopg's async mode can't use Windows' default (Proactor) event loop.
    if sys.platform == "win32":
        return {"selector": asyncio.SelectorEventLoop}
    return {"default": asyncio.new_event_loop}


@pytest.fixture(scope="session")
def db_url() -> Iterator[str]:
    """A throwaway Postgres with every migration applied, in order."""
    with PostgresContainer(IMAGE, driver=None) as pg:
        url = pg.get_connection_url()
        with psycopg.connect(url, autocommit=True) as c:
            migrate(c)
        yield url


@pytest.fixture
def conn(db_url: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(db_url, autocommit=True) as c:
        register_vector(c)
        yield c


@pytest.fixture(scope="session")
def shared_embedder() -> Embedder:
    return Embedder()


@pytest.fixture(params=["postgres", "sqlite"])
async def any_store(request, shared_embedder, tmp_path) -> AsyncIterator[Store]:
    """The same test against both databases (ADR 0019): they must behave the same."""
    if request.param == "postgres":
        url = request.getfixturevalue("db_url")
        async with AsyncConnectionPool(url, configure=register_vector_async, open=False) as pool:
            yield PostgresStore(pool, shared_embedder)
    else:
        store = SqliteStore(tmp_path / "memory.db", shared_embedder)
        yield store
        store.close()
