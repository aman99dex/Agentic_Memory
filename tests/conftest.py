import asyncio
import sys
from collections.abc import Iterator

import psycopg
import pytest
from pgvector.psycopg import register_vector
from testcontainers.community.postgres import PostgresContainer

from agentic_memory.migrate import migrate

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
