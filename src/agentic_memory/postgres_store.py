"""PostgresStore: the server store (ADRs 0001-0005, 0016, 0020-0021, 0023)."""

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import numpy as np
from psycopg import AsyncConnection
from psycopg.rows import class_row
from psycopg_pool import AsyncConnectionPool

from agentic_memory.embedder import Embedder
from agentic_memory.fusion import rrf_scores
from agentic_memory.rank import Rank
from agentic_memory.store import (
    MAX_ATTEMPTS,
    SEARCHABLE,
    STATS_KEYS,
    STATS_SQL,
    Kind,
    Memory,
    Retriever,
    Turn,
    turn_hash,
)

# A fact is visible at time T if it had started and not yet ended. as_of = NULL means "now":
# 'infinity' makes valid_to > T false for every closed fact, so only current ones remain.
# Archived facts (ADR 0016) are hidden from search but still exist and still show in history.
# With a namespace, the user-wide facts (namespace NULL) and that namespace's are visible
# (ADR 0021); with none, all of the user's are.
VISIBLE = """user_id = %(user_id)s
    AND archived_at IS NULL
    AND valid_from <= coalesce(%(as_of)s::timestamptz, 'infinity')
    AND (valid_to IS NULL OR valid_to > coalesce(%(as_of)s::timestamptz, 'infinity'))
    AND (%(ns)s::text IS NULL OR namespace IS NULL OR namespace = %(ns)s)
    AND kind = ANY(%(kinds)s)"""

# Keyword search tries every word (AND) first and falls back to any word (OR) only when AND
# matches nothing (ADR 0011). 'which city do I live in?': 'citi' & 'live', then 'citi' | 'live'.

# Every f-string below only glues in these constants. User input never enters the SQL text:
# it travels separately as a bound %(name)s parameter, so it can't be read as SQL.
AND_TSQUERY = "websearch_to_tsquery('english', %(query)s)"
OR_TSQUERY = """(SELECT string_agg(quote_literal(w), ' | ')
    FROM unnest(tsvector_to_array(to_tsvector('english', %(query)s))) w)::tsquery"""

# Every version of the given facts: walk superseded_by forwards and backwards (ADR 0004).
CHAIN = """WITH RECURSIVE chain AS (
    SELECT id FROM memories WHERE user_id = %(u)s AND id = ANY(%(ids)s)
    UNION
    SELECT m.id FROM memories m, chain c
    WHERE m.user_id = %(u)s
      AND (m.superseded_by = c.id
           OR m.id = (SELECT superseded_by FROM memories WHERE id = c.id)))"""

COLUMNS = """id, user_id, content, source_turn_ids, valid_from, valid_to, superseded_by,
    created_at, namespace, kind"""
TURN_COLUMNS = "id, session_id, user_text, assistant_text, occurred_at, namespace"


class PostgresStore:
    def __init__(self, pool: AsyncConnectionPool, embedder: Embedder) -> None:
        self._pool = pool
        self._embedder = embedder

    async def _embed(self, text: str) -> np.ndarray:
        # The model is CPU work that would freeze every other request; run it on a thread.
        (vector,) = await asyncio.to_thread(self._embedder.embed, [text])
        return np.array(vector, dtype=np.float32)

    # ---- turns and the queue (ADR 0020) ----

    async def add_turn(
        self,
        user_id: str,
        session_id: str,
        user_text: str,
        assistant_text: str,
        occurred_at: datetime,
        namespace: str | None = None,
    ) -> tuple[int, bool]:
        """Store one round, unprocessed. Returns (turn id, True if new); a repeat is stored once."""
        content_hash = turn_hash(session_id, occurred_at, user_text, assistant_text)
        async with self._pool.connection() as conn:
            # The unique index, not a check-then-insert, is what makes this safe under races.
            cur = await conn.execute(
                """INSERT INTO turns (user_id, session_id, user_text, assistant_text,
                                      occurred_at, content_hash, namespace)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (user_id, content_hash) DO NOTHING RETURNING id""",
                (
                    user_id,
                    session_id,
                    user_text,
                    assistant_text,
                    occurred_at,
                    content_hash,
                    namespace,
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

    async def get_turns(self, user_id: str, turn_ids: Sequence[int]) -> list[Turn]:
        """The original rounds, in the order asked for. Ids that aren't this user's are skipped."""
        async with self._pool.connection() as conn:
            cur = conn.cursor(row_factory=class_row(Turn))
            await cur.execute(
                f"SELECT {TURN_COLUMNS} FROM turns WHERE user_id = %s AND id = ANY(%s)",  # noqa: S608
                (user_id, list(turn_ids)),
            )
            rows = {t.id: t for t in await cur.fetchall()}
        return [rows[i] for i in turn_ids if i in rows]

    async def turn_processed(self, user_id: str, turn_id: int) -> bool:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT processed_at IS NOT NULL FROM turns WHERE user_id = %s AND id = %s",
                (user_id, turn_id),
            )
            row = await cur.fetchone()
        return bool(row and row[0])

    async def pending_users(self, limit: int = 100) -> list[str]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT DISTINCT user_id FROM turns
                   WHERE processed_at IS NULL AND attempts < %s LIMIT %s""",
                (MAX_ATTEMPTS, limit),
            )
            return [r[0] for r in await cur.fetchall()]

    async def pending_turns(self, user_id: str, limit: int = 20) -> list[Turn]:
        """Oldest first: facts must be applied in the order they became true."""
        async with self._pool.connection() as conn:
            cur = conn.cursor(row_factory=class_row(Turn))
            await cur.execute(
                f"""SELECT {TURN_COLUMNS} FROM turns
                    WHERE user_id = %s AND processed_at IS NULL AND attempts < %s
                    ORDER BY occurred_at, id LIMIT %s""",  # noqa: S608
                (user_id, MAX_ATTEMPTS, limit),
            )
            return await cur.fetchall()

    async def finish_turn(self, turn_id: int, now: datetime, error: str | None = None) -> None:
        async with self._pool.connection() as conn:
            if error is None:
                await conn.execute(
                    "UPDATE turns SET processed_at = %s, last_error = NULL WHERE id = %s",
                    (now, turn_id),
                )
            else:
                await conn.execute(
                    "UPDATE turns SET attempts = attempts + 1, last_error = %s WHERE id = %s",
                    (error[:1000], turn_id),
                )

    async def claim(self, user_id: str, holder: str, now: datetime, seconds: float) -> bool:
        """Take (or renew) this user's lease. False while another live worker holds it."""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """INSERT INTO ingest_leases (user_id, holder, expires_at) VALUES (%s, %s, %s)
                   ON CONFLICT (user_id) DO UPDATE
                       SET holder = EXCLUDED.holder, expires_at = EXCLUDED.expires_at
                       WHERE ingest_leases.expires_at < %s
                          OR ingest_leases.holder = EXCLUDED.holder
                   RETURNING user_id""",
                (user_id, holder, now + timedelta(seconds=seconds), now),
            )
            return await cur.fetchone() is not None

    async def release(self, user_id: str, holder: str) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "DELETE FROM ingest_leases WHERE user_id = %s AND holder = %s", (user_id, holder)
            )

    async def session_turns(self, user_id: str, session_id: str, after_id: int = 0) -> list[Turn]:
        async with self._pool.connection() as conn:
            cur = conn.cursor(row_factory=class_row(Turn))
            await cur.execute(
                f"""SELECT {TURN_COLUMNS} FROM turns
                    WHERE user_id = %s AND session_id = %s AND id > %s
                    ORDER BY occurred_at, id""",  # noqa: S608
                (user_id, session_id, after_id),
            )
            return await cur.fetchall()

    # ---- facts ----

    async def add(
        self,
        user_id: str,
        content: str,
        source_turn_ids: Sequence[int],
        valid_from: datetime,
        namespace: str | None = None,
    ) -> int:
        embedding = await self._embed(content)
        async with self._pool.connection() as conn:
            return await self._insert(
                conn, user_id, content, source_turn_ids, valid_from, embedding, namespace
            )

    async def _insert(
        self,
        conn: AsyncConnection,
        user_id: str,
        content: str,
        source_turn_ids: Sequence[int],
        valid_from: datetime,
        embedding: np.ndarray,
        namespace: str | None = None,
        kind: Kind = "fact",
        session_id: str | None = None,
    ) -> int:
        cur = await conn.execute(
            """INSERT INTO memories (user_id, content, source_turn_ids, valid_from, embedding,
                                     embedding_model, namespace, kind, session_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (
                user_id,
                content,
                list(source_turn_ids),
                valid_from,
                embedding,
                self._embedder.model_name,
                namespace,
                kind,
                session_id,
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
        # greatest(): a turn that arrives late, older than the fact it replaces, closes the
        # fact at its own start instead of breaking the valid_to >= valid_from check.
        cur = await conn.execute(
            """UPDATE memories SET valid_to = greatest(%s, valid_from), superseded_by = %s
               WHERE id = %s AND user_id = %s AND valid_to IS NULL AND kind = 'fact'""",
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
        namespace: str | None = None,
    ) -> int:
        """Insert the new fact and close the old one in one transaction (Lesson 1.3)."""
        embedding = await self._embed(content)
        async with self._pool.connection() as conn, conn.transaction():
            new_id = await self._insert(
                conn, user_id, content, source_turn_ids, valid_from, embedding, namespace
            )
            if not await self._close(conn, user_id, old_id, valid_from, new_id):
                raise LookupError(f"memory {old_id} is not a current fact of {user_id}")
            return new_id

    async def history(self, user_id: str, memory_id: int) -> list[Memory]:
        """Every version of one fact, oldest first (the chain linked by superseded_by)."""
        async with self._pool.connection() as conn:
            cur = conn.cursor(row_factory=class_row(Memory))
            await cur.execute(
                f"""{CHAIN} SELECT {COLUMNS} FROM memories
                    WHERE id IN (SELECT id FROM chain) ORDER BY valid_from, id""",  # noqa: S608
                {"u": user_id, "ids": [memory_id]},
            )
            return await cur.fetchall()

    async def drop(self, user_id: str, memory_id: int) -> bool:
        """Delete one fact row (no history, no turns). Only for the history-off ablation."""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "DELETE FROM memories WHERE id = %s AND user_id = %s", (memory_id, user_id)
            )
            return cur.rowcount == 1

    async def forget(
        self, user_id: str, memory_ids: Sequence[int] | None = None
    ) -> tuple[int, int]:
        """Hard delete (ADR 0004, 0014). Returns (memories, turns) deleted.

        With ids: those facts, every older/newer version of them, the turns they came from,
        and every other fact or episode that came from those turns (their source words are
        gone). With None: everything this user has. Profiles are summaries of facts, so they
        are dropped either way and rebuilt from what remains (ADR 0021).
        """
        params = {"u": user_id, "ids": list(memory_ids or [])}
        async with self._pool.connection() as conn, conn.transaction():
            await conn.execute(
                "DELETE FROM memories WHERE user_id = %s AND kind = 'profile'", (user_id,)
            )
            if memory_ids is None:
                mems = (
                    await conn.execute("DELETE FROM memories WHERE user_id = %s", (user_id,))
                ).rowcount
                turns = (
                    await conn.execute("DELETE FROM turns WHERE user_id = %s", (user_id,))
                ).rowcount
            else:
                cur = await conn.execute(
                    f"""{CHAIN} SELECT coalesce(array_agg(DISTINCT t), '{{}}'::bigint[])
                        FROM memories m, unnest(m.source_turn_ids) t
                        WHERE m.id IN (SELECT id FROM chain)""",  # noqa: S608
                    params,
                )
                row = await cur.fetchone()
                assert row is not None
                turn_ids: list[int] = row[0]
                # One statement: its snapshot still sees the chain while the rows are removed.
                cur = await conn.execute(
                    f"""{CHAIN} DELETE FROM memories WHERE user_id = %(u)s
                        AND (id IN (SELECT id FROM chain)
                             OR source_turn_ids && %(turns)s::bigint[])""",  # noqa: S608
                    {**params, "turns": turn_ids},
                )
                mems = cur.rowcount
                cur = await conn.execute(
                    "DELETE FROM turns WHERE user_id = %s AND id = ANY(%s)", (user_id, turn_ids)
                )
                turns = cur.rowcount
            await conn.execute(
                "INSERT INTO deletions (user_id, memories, turns) VALUES (%s, %s, %s)",
                (user_id, mems, turns),
            )
        return mems, turns

    # ---- ranking (ADR 0016) ----

    async def _rerank(
        self, ids: list[int], scores: dict[int, float], rank: Rank, now: datetime
    ) -> list[int]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT id, importance, coalesce(last_accessed_at, valid_from)
                   FROM memories WHERE id = ANY(%s)""",
                (ids,),
            )
            rows = await cur.fetchall()
        adjusted = {
            i: scores[i] * rank.factor(float(imp), (now - touched).total_seconds() / 86400)
            for i, imp, touched in rows
        }
        return sorted(ids, key=adjusted.__getitem__, reverse=True)

    async def touch(
        self, user_id: str, memory_ids: Sequence[int], at: datetime, rank: Rank
    ) -> None:
        """Record that these facts were just recalled: more important, and fresh again."""
        if not memory_ids:
            return
        async with self._pool.connection() as conn:
            await conn.execute(
                """UPDATE memories
                   SET importance = least(importance + %s, %s), last_accessed_at = %s
                   WHERE user_id = %s AND id = ANY(%s)""",
                (rank.access_boost, rank.importance_cap, at, user_id, list(memory_ids)),
            )

    async def unarchive(self, user_id: str, memory_ids: Sequence[int]) -> int:
        """Bring archived facts back into search. Returns how many were restored."""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """UPDATE memories SET archived_at = NULL
                   WHERE user_id = %s AND id = ANY(%s) AND archived_at IS NOT NULL""",
                (user_id, list(memory_ids)),
            )
            return cur.rowcount

    # ---- search ----

    async def search_vector(
        self,
        user_id: str,
        query: str,
        limit: int = 20,
        as_of: datetime | None = None,
        namespace: str | None = None,
        kinds: Sequence[Kind] = SEARCHABLE,
    ) -> list[int]:
        embedding = await self._embed(query)
        params = {
            "embedding": embedding,
            "user_id": user_id,
            "as_of": as_of,
            "limit": limit,
            "ns": namespace,
            "kinds": list(kinds),
        }
        async with self._pool.connection() as conn, conn.transaction():
            # Without this, a tenant filter after HNSW's 40 candidates comes back short (ADR 0001).
            await conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
            # relaxed_order can return rows slightly out of order, so sort again outside.
            cur = await conn.execute(
                f"""WITH hits AS MATERIALIZED (
                        SELECT id, embedding <=> %(embedding)s AS distance FROM memories
                        WHERE {VISIBLE} ORDER BY distance LIMIT %(limit)s)
                    SELECT id FROM hits ORDER BY distance""",  # noqa: S608
                params,
            )
            return [row[0] for row in await cur.fetchall()]

    async def search_text(
        self,
        user_id: str,
        query: str,
        limit: int = 20,
        as_of: datetime | None = None,
        namespace: str | None = None,
        kinds: Sequence[Kind] = SEARCHABLE,
    ) -> list[int]:
        params = {
            "query": query,
            "user_id": user_id,
            "as_of": as_of,
            "limit": limit,
            "ns": namespace,
            "kinds": list(kinds),
        }
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
        self,
        user_id: str,
        query: str,
        limit: int = 10,
        as_of: datetime | None = None,
        retriever: Retriever = "hybrid",
        rank: Rank | None = None,
        now: datetime | None = None,
        namespace: str | None = None,
        kinds: Sequence[Kind] = SEARCHABLE,
    ) -> list[Memory]:
        """Hybrid search: both retrievers at once, fused with RRF (ADR 0005).

        `retriever` exists so the evaluation can switch one half off (ablation, ADR 0015).
        With `rank`, the fused candidates are reordered by importance and recency (ADR 0016);
        `now` is the moment age is measured from (default: the real clock).
        """
        scope = (20, as_of, namespace, kinds)  # limit per retriever, then the filters
        if retriever == "vector":
            rankings = [await self.search_vector(user_id, query, *scope)]
        elif retriever == "text":
            rankings = [await self.search_text(user_id, query, *scope)]
        else:
            by_meaning, by_words = await asyncio.gather(
                self.search_vector(user_id, query, *scope),
                self.search_text(user_id, query, *scope),
            )
            rankings = [by_meaning, by_words]
        scores = rrf_scores(rankings)
        ids = sorted(scores, key=scores.__getitem__, reverse=True)
        if rank is not None and ids:
            ids = await self._rerank(ids, scores, rank, now or datetime.now(UTC))
        ids = ids[:limit]
        async with self._pool.connection() as conn:
            cur = conn.cursor(row_factory=class_row(Memory))
            await cur.execute(
                f"SELECT {COLUMNS} FROM memories WHERE id = ANY(%s)",  # noqa: S608
                (ids,),
            )
            rows = {m.id: m for m in await cur.fetchall()}
        return [rows[i] for i in ids]

    # ---- episodes and profiles (ADR 0021) ----

    async def derived(
        self, user_id: str, kind: Kind, namespace: str | None = None, session_id: str | None = None
    ) -> Memory | None:
        """The one episode of a session, or the one profile of (user, namespace)."""
        async with self._pool.connection() as conn:
            cur = conn.cursor(row_factory=class_row(Memory))
            if kind == "episode":
                await cur.execute(
                    f"""SELECT {COLUMNS} FROM memories
                        WHERE user_id = %s AND kind = 'episode' AND session_id = %s""",  # noqa: S608
                    (user_id, session_id),
                )
            else:
                await cur.execute(
                    f"""SELECT {COLUMNS} FROM memories WHERE user_id = %s AND kind = %s
                        AND namespace IS NOT DISTINCT FROM %s""",  # noqa: S608
                    (user_id, kind, namespace),
                )
            return await cur.fetchone()

    async def put_derived(
        self,
        user_id: str,
        kind: Kind,
        content: str,
        source_turn_ids: Sequence[int],
        valid_from: datetime,
        namespace: str | None = None,
        session_id: str | None = None,
    ) -> int:
        """Create or rewrite in place. A summary is derived data, not a fact with a lifetime,
        so it has no versions: the turns and facts it came from are the record."""
        embedding = await self._embed(content)
        existing = await self.derived(user_id, kind, namespace, session_id)
        async with self._pool.connection() as conn:
            if existing is None:
                return await self._insert(
                    conn,
                    user_id,
                    content,
                    source_turn_ids,
                    valid_from,
                    embedding,
                    namespace,
                    kind,
                    session_id,
                )
            await conn.execute(
                """UPDATE memories SET content = %s, source_turn_ids = %s, embedding = %s
                   WHERE id = %s""",
                (content, list(source_turn_ids), embedding, existing.id),
            )
            return existing.id

    async def current_facts(
        self, user_id: str, namespace: str | None, limit: int = 200
    ) -> list[Memory]:
        """Current facts of exactly this scope (NULL = user-wide), most used and newest first."""
        async with self._pool.connection() as conn:
            cur = conn.cursor(row_factory=class_row(Memory))
            await cur.execute(
                f"""SELECT {COLUMNS} FROM memories
                    WHERE user_id = %s AND kind = 'fact' AND valid_to IS NULL
                      AND archived_at IS NULL AND namespace IS NOT DISTINCT FROM %s
                    ORDER BY importance DESC, valid_from DESC, id DESC LIMIT %s""",  # noqa: S608
                (user_id, namespace, limit),
            )
            return await cur.fetchall()

    async def recent_episodes(
        self, user_id: str, namespace: str | None, limit: int = 3
    ) -> list[Memory]:
        """The latest session summaries: of one namespace, or of every one when None."""
        async with self._pool.connection() as conn:
            cur = conn.cursor(row_factory=class_row(Memory))
            await cur.execute(
                f"""SELECT {COLUMNS} FROM memories
                    WHERE user_id = %s AND kind = 'episode'
                      AND (%s::text IS NULL OR namespace = %s)
                    ORDER BY valid_from DESC, id DESC LIMIT %s""",  # noqa: S608
                (user_id, namespace, namespace, limit),
            )
            return await cur.fetchall()

    # ---- tenant keys (ADR 0023) ----

    async def add_api_key(self, tenant: str, key_sha256: str) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "INSERT INTO api_keys (tenant, key_sha256) VALUES (%s, %s)", (tenant, key_sha256)
            )

    async def tenant_for_key(self, key_sha256: str) -> str | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT tenant FROM api_keys WHERE key_sha256 = %s AND revoked_at IS NULL",
                (key_sha256,),
            )
            row = await cur.fetchone()
        return str(row[0]) if row else None

    async def revoke_api_keys(self, tenant: str, now: datetime) -> int:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE api_keys SET revoked_at = %s WHERE tenant = %s AND revoked_at IS NULL",
                (now, tenant),
            )
            return cur.rowcount

    async def stats(self, user_id: str) -> dict[str, object]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(STATS_SQL.format(p="%(u)s"), {"u": user_id})
            row = await cur.fetchone()
        assert row is not None
        return dict(zip(STATS_KEYS, row, strict=True))


MemoryStore = PostgresStore  # the name the evaluation and older tests use
