from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from pgvector.psycopg import register_vector
from testcontainers.community.postgres import PostgresContainer

IMAGE = "pgvector/pgvector:0.8.7-pg18"
MIGRATIONS = Path(__file__).parent.parent / "migrations"


@pytest.fixture(scope="session")
def db_url() -> Iterator[str]:
    """A throwaway Postgres with every migration applied, in order."""
    with PostgresContainer(IMAGE, driver=None) as pg:
        url = pg.get_connection_url()
        with psycopg.connect(url, autocommit=True) as c:
            for f in sorted(MIGRATIONS.glob("*.sql")):
                c.execute(f.read_text())
        yield url


@pytest.fixture
def conn(db_url: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(db_url, autocommit=True) as c:
        register_vector(c)
        yield c
