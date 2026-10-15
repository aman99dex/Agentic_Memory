"""Phase 0: the database we develop against has the pgvector features the design relies on."""

import psycopg
import pytest
from testcontainers.community.postgres import PostgresContainer

IMAGE = "pgvector/pgvector:0.8.7-pg18"


@pytest.fixture(scope="module")
def conn():
    with PostgresContainer(IMAGE, driver=None) as pg, psycopg.connect(pg.get_connection_url()) as c:
        c.execute("CREATE EXTENSION IF NOT EXISTS vector")
        yield c


def test_pgvector_version_has_cve_fix_and_iterative_scan(conn):
    (version,) = conn.execute(
        "SELECT extversion FROM pg_extension WHERE extname='vector'"
    ).fetchone()
    # >= 0.8.2: CVE-2026-3172 fix; iterative scans need >= 0.8.0 (ADR 0001)
    assert tuple(map(int, version.split("."))) >= (0, 8, 2)
    conn.execute("SET hnsw.iterative_scan = relaxed_order")


def test_fulltext_stems_and_drops_stopwords(conn):
    (tsv,) = conn.execute("SELECT to_tsvector('english', 'I moved to Pune')::text").fetchone()
    assert tsv == "'move':2 'pune':4"
