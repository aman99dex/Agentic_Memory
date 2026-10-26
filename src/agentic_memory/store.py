"""MemoryStore: the only code that talks SQL about memories (ADRs 0001-0005)."""

import asyncio
import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from psycopg import AsyncConnection
from psycopg.rows import class_row
from psycopg_pool import AsyncConnectionPool

from agentic_memory.embedder import Embedder
from agentic_memory.fusion import rrf

# A fact is visible at time T if it had started and not yet ended. as_of = NULL means "now":
# 'infinity' makes valid_to > T false for every closed fact, so only current ones remain.
VISIBLE = """user_id = %(user_id)s
    AND valid_from <= coalesce(%(as_of)s::timestamptz, 'infinity')
    AND (valid_to IS NULL OR valid_to > coalesce(%(as_of)s::timestamptz, 'infinity'))"""

# Keyword search tries every word (AND) first and falls back to any word (OR) only when AND
# matches nothing (ADR 0011). 'which city do I live in?': 'citi' & 'live', then 'citi' | 'live'.

# Every f-string below only glues in these constants. User input never enters the SQL text:
# it travels separately as a bound %(name)s parameter, so it can't be read as SQL.
AND_TSQUERY = "websearch_to_tsquery('english', %(query)s)"
OR_TSQUERY = """(SELECT string_agg(quote_literal(w), ' | ')
    FROM unnest(tsvector_to_array(to_tsvector('english', %(query)s))) w)::tsquery"""

COLUMNS = "id, user_id, content, source_turn_ids, valid_from, valid_to, superseded_by, created_at"


@dataclass(frozen=True)
class Memory:
    id: int
    user_id: str
    content: str
    source_turn_ids: list[int]
    valid_from: datetime
    valid_to: datetime | None
    superseded_by: int | None
    created_at: datetime


class MemoryStore:
    def __init__(self, pool: AsyncConnectionPool, embedder: Embedder) -> None:
        self._pool = pool
        self._embedder = embedder

    async def _embed(self, text: str) -> np.ndarray:
        # The model is CPU work that would freeze every other request; run it on a thread.
        (vector,) = await asyncio.to_thread(self._embedder.embed, [text])
        return np.array(vector, dtype=np.float32)

    async def add_turn(
        self,
        user_id: str,
        session_id: str,
        user_text: str,
        assistant_text: str,
        occurred_at: datetime,
    ) -> tuple[int, bool]:
        """Store one round. Returns (turn id, True if new). The same round twice is stored once."""
        # JSON keeps field boundaries: ("ab", "c") and ("a", "bc") must not hash the same.
        fingerprint = json.dumps([session_id, occurred_at.isoformat(), user_text, assistant_text])
        content_hash = hashlib.sha256(fingerprint.encode()).hexdigest()
        embedding = await self._embed(f"{user_text} {assistant_text}")
        async with self._pool.connection() as conn:
            # The unique index, not a check-then-insert, is what makes this safe under races.
            cur = await conn.execute(
                """INSERT INTO turns (user_id, session_id, user_text, assistant_text,
                                      occurred_at, content_hash, embedding, embedding_model)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (user_id, content_hash) DO NOTHING RETURNING id""",
                (
                    user_id,
                    session_id,
                    user_text,
                    assistant_text,
                    occurred_at,
                    content_hash,
                    embedding,
                    self._embedder.model_name,
                ),
            )
            if row := await cur.fetchone():
                return int(row[0]), True
            # A repeat: DO NOTHING returned no row. A fresh statement sees the first copy.
            cur = await conn.execute(
                "SELECT id FROM turns WHERE user_id = %s AND content_hash = %s",
                (user_id, content_hash),
            )
            row = await cur.fetchone()
        assert row is not None
        return int(row[0]), False

    async def turn_has_memories(self, user_id: str, turn_id: int) -> bool:
        """True if any fact was written from this turn (a crash before that leaves it False)."""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT EXISTS (SELECT 1 FROM memories
                          WHERE user_id = %s AND %s = ANY(source_turn_ids))""",
                (user_id, turn_id),
            )
            row = await cur.fetchone()
        assert row is not None
        return bool(row[0])

    async def add(
        self, user_id: str, content: str, source_turn_ids: Sequence[int], valid_from: datetime
    ) -> int:
        embedding = await self._embed(content)
        async with self._pool.connection() as conn:
            return await self._insert(
                conn, user_id, content, source_turn_ids, valid_from, embedding
            )

    async def _insert(
        self,
        conn: AsyncConnection,
        user_id: str,
        content: str,
        source_turn_ids: Sequence[int],
        valid_from: datetime,
        embedding: np.ndarray,
    ) -> int:
        cur = await conn.execute(
            """INSERT INTO memories
                   (user_id, content, source_turn_ids, valid_from, embedding, embedding_model)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
            (
                user_id,
                content,
                list(source_turn_ids),
                valid_from,
                embedding,
                self._embedder.model_name,
            ),
        )
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    async def get(self, user_id: str, memory_id: int) -> Memory | None:
        async with self._pool.connection() as conn:
            cur = conn.cursor(row_factory=class_row(Memory))
            await cur.execute(
                f"SELECT {COLUMNS} FROM memories WHERE id = %s AND user_id = %s",  # noqa: S608
                (memory_id, user_id),
            )
            return await cur.fetchone()

    async def invalidate(
        self, user_id: str, memory_id: int, at: datetime, superseded_by: int | None = None
    ) -> bool:
        """Close a current fact. False if it doesn't exist, isn't yours, or is already closed."""
        async with self._pool.connection() as conn:
            return await self._close(conn, user_id, memory_id, at, superseded_by)

    async def _close(
        self,
        conn: AsyncConnection,
        user_id: str,
        memory_id: int,
        at: datetime,
        superseded_by: int | None,
    ) -> bool:
        cur = await conn.execute(
            """UPDATE memories SET valid_to = %s, superseded_by = %s
               WHERE id = %s AND user_id = %s AND valid_to IS NULL""",
            (at, superseded_by, memory_id, user_id),
        )
        return cur.rowcount == 1

    async def supersede(
        self,
        user_id: str,
        old_id: int,
        content: str,
        source_turn_ids: Sequence[int],
        valid_from: datetime,
    ) -> int:
        """Insert the new fact and close the old one in one transaction (Lesson 1.3)."""
        embedding = await self._embed(content)
        async with self._pool.connection() as conn, conn.transaction():
            new_id = await self._insert(
                conn, user_id, content, source_turn_ids, valid_from, embedding
            )
            if not await self._close(conn, user_id, old_id, valid_from, new_id):
                raise LookupError(f"memory {old_id} is not a current fact of {user_id}")
            return new_id

    async def search_vector(
        self, user_id: str, query: str, limit: int = 20, as_of: datetime | None = None
    ) -> list[int]:
        embedding = await self._embed(query)
        async with self._pool.connection() as conn, conn.transaction():
            # Without this, a tenant filter after HNSW's 40 candidates comes back short (ADR 0001).
            await conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
            # relaxed_order can return rows slightly out of order, so sort again outside.
            cur = await conn.execute(
                f"""WITH hits AS MATERIALIZED (
                        SELECT id, embedding <=> %(embedding)s AS distance FROM memories
                        WHERE {VISIBLE} ORDER BY distance LIMIT %(limit)s)
                    SELECT id FROM hits ORDER BY distance""",  # noqa: S608
                {"embedding": embedding, "user_id": user_id, "as_of": as_of, "limit": limit},
            )
            return [row[0] for row in await cur.fetchall()]

    async def search_text(
        self, user_id: str, query: str, limit: int = 20, as_of: datetime | None = None
    ) -> list[int]:
        params = {"query": query, "user_id": user_id, "as_of": as_of, "limit": limit}
        async with self._pool.connection() as conn:
            for tsquery in (AND_TSQUERY, OR_TSQUERY):
                cur = await conn.execute(
                    f"""SELECT id FROM memories, (SELECT {tsquery} AS q) AS t
                        WHERE {VISIBLE} AND tsv @@ q
                        ORDER BY ts_rank_cd(tsv, q) DESC, id LIMIT %(limit)s""",  # noqa: S608
                    params,
                )
                if ids := [row[0] for row in await cur.fetchall()]:
                    return ids
        return []

    async def search(
        self, user_id: str, query: str, limit: int = 10, as_of: datetime | None = None
    ) -> list[Memory]:
        """Hybrid search: both retrievers at once, fused with RRF (ADR 0005)."""
        by_meaning, by_words = await asyncio.gather(
            self.search_vector(user_id, query, as_of=as_of),
            self.search_text(user_id, query, as_of=as_of),
        )
        ids = rrf([by_meaning, by_words])[:limit]
        async with self._pool.connection() as conn:
            cur = conn.cursor(row_factory=class_row(Memory))
            await cur.execute(
                f"SELECT {COLUMNS} FROM memories WHERE id = ANY(%s)",  # noqa: S608
                (ids,),
            )
            rows = {m.id: m for m in await cur.fetchall()}
        return [rows[i] for i in ids]
