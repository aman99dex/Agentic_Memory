"""The filtered-HNSW trap (ADR 0001).

HNSW keeps only ef_search (40) candidates, and WHERE user_id = ... is applied after.
With 100 users, ~0.4 of those 40 belong to the user we asked about, so LIMIT 10 comes
back short. Iterative scan keeps walking the graph until 10 rows pass the filter.
"""

import datetime

import numpy as np
import pytest

USERS, PER_USER = 100, 50
QUERY = """SELECT count(*) FROM (
    SELECT 1 FROM memories WHERE user_id = 'user7' ORDER BY embedding <=> %s LIMIT 10) t"""


@pytest.fixture
def crowded(conn):
    rng = np.random.default_rng(7)

    def unit():
        v = rng.standard_normal(384).astype(np.float32)
        return v / np.linalg.norm(v)

    now = datetime.datetime.now(datetime.UTC)
    copy = "COPY memories (user_id, content, source_turn_ids, valid_from, embedding) FROM STDIN"
    with conn.cursor().copy(copy + " WITH (FORMAT BINARY)") as cp:
        cp.set_types(["text", "text", "int8[]", "timestamptz", "vector"])
        for u in range(USERS):
            for i in range(PER_USER):
                cp.write_row([f"user{u}", f"memory {i}", [], now, unit()])
    conn.execute("ANALYZE memories")
    conn.execute("SET enable_seqscan = off")  # at real scale the planner picks the index anyway
    yield unit()
    conn.execute("TRUNCATE memories")


def test_without_iterative_scan_tenant_query_comes_back_short(conn, crowded):
    conn.execute("SET hnsw.iterative_scan = off")
    (n,) = conn.execute(QUERY, (crowded,)).fetchone()
    assert n < 10  # the bug: user7 has 50 memories, we asked for 10


def test_iterative_scan_returns_the_full_page(conn, crowded):
    conn.execute("SET hnsw.iterative_scan = relaxed_order")
    (n,) = conn.execute(QUERY, (crowded,)).fetchone()
    assert n == 10
