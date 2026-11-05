"""SqliteStore: the zero-setup local store (ADR 0019). Same behaviour as PostgresStore.

- Words: FTS5 with Porter stemming, ranked by real BM25 (AND first, then OR, as ADR 0011).
- Meaning: exact cosine over every visible vector with numpy. No extension to install, and
  no approximate-search misses.
- One connection per process, used one call at a time (an asyncio lock, the work on a thread).
  WAL mode + a busy timeout let a hook, the MCP server and a worker share one file.
"""

import asyncio
import json
import re
import sqlite3
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from importlib import resources
from pathlib import Path
from typing import Any

import numpy as np

from agentic_memory.embedder import DIM, Embedder
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

FOREVER = "9999-12-31T00:00:00.000000+00:00"  # plays Postgres' 'infinity' in comparisons

# Words that carry no meaning for search. Postgres' 'english' config drops these itself.
STOPWORDS = frozenset(
    """a an and are as at be but by can could did do does for from had has have how i if in
    into is it its me my no not of on or our she so than that the their them then there
    these they this to was we were what when where which who why will with would you your
    about any after all also am been being before both each he her him his just more most
    other over own same some such too under until up very""".split()
)

VISIBLE = """user_id = :user_id
    AND archived_at IS NULL
    AND valid_from <= coalesce(:as_of, '9999-12-31')
    AND (valid_to IS NULL OR valid_to > coalesce(:as_of, '9999-12-31'))
    AND (:ns IS NULL OR namespace IS NULL OR namespace = :ns)
    AND kind IN (SELECT value FROM json_each(:kinds))"""

CHAIN = """WITH RECURSIVE chain(id) AS (
    SELECT id FROM memories WHERE user_id = :u AND id IN (SELECT value FROM json_each(:ids))
    UNION
    SELECT m.id FROM memories m, chain c
    WHERE m.user_id = :u
      AND (m.superseded_by = c.id
           OR m.id = (SELECT superseded_by FROM memories WHERE id = c.id)))"""

COLUMNS = """id, user_id, content, source_turn_ids, valid_from, valid_to, superseded_by,
    created_at, namespace, kind"""
TURN_COLUMNS = "id, session_id, user_text, assistant_text, occurred_at, namespace"


def _ts(d: datetime) -> str:
    """Fixed-width UTC text, so text order is time order."""
    return d.astimezone(UTC).isoformat(timespec="microseconds")


def _dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def _memory(row: sqlite3.Row) -> Memory:
    valid_from = _dt(row[4])
    created_at = _dt(row[7])
    assert valid_from is not None and created_at is not None
    return Memory(
        row[0],
        row[1],
        row[2],
        json.loads(row[3]),
        valid_from,
        _dt(row[5]),
        row[6],
        created_at,
        row[8],
        row[9],
    )


def _turn(row: sqlite3.Row) -> Turn:
    occurred_at = _dt(row[4])
    assert occurred_at is not None
    return Turn(row[0], row[1], row[2], row[3], occurred_at, row[5])


def _fts_queries(text: str) -> tuple[str, str] | None:
    """(every word, any word) as FTS5 queries. Words are quoted, so no input is read as syntax."""
    words = [w for w in re.findall(r"\w+", text.lower()) if w not in STOPWORDS]
    if not words:
        return None
    quoted = [f'"{w}"' for w in dict.fromkeys(words)]
    return " ".join(quoted), " OR ".join(quoted)


def _statements(sql: str) -> Iterator[str]:
    """Split a script into statements; complete_statement knows a trigger's inner `;`."""
    buf = ""
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            if buf.strip():
                yield buf
            buf = ""


def migrate(db: sqlite3.Connection) -> list[str]:
    """Apply migrations/sqlite/NNNN_*.sql not yet applied; user_version counts applied files.

    Local mode migrates when it opens (unlike the server, ADR 0010): there is no deploy step,
    and BEGIN IMMEDIATE makes two processes opening a new file at once take turns.
    """
    files = sorted(
        (
            f
            for f in resources.files("agentic_memory").joinpath("migrations/sqlite").iterdir()
            if f.name.endswith(".sql")
        ),
        key=lambda f: f.name,
    )
    db.execute("BEGIN IMMEDIATE")
    try:
        done = db.execute("PRAGMA user_version").fetchone()[0]
        for f in files[done:]:
            for statement in _statements(f.read_text(encoding="utf-8")):
                db.execute(statement)
        db.execute(f"PRAGMA user_version = {len(files)}")
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise
    return [f.name for f in files[done:]]


class SqliteStore:
    def __init__(self, path: str | Path, embedder: Embedder) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None: no hidden transactions; every write says BEGIN itself.
        self._db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        for pragma in (
            "journal_mode = WAL",
            "busy_timeout = 10000",
            "foreign_keys = ON",
            "synchronous = NORMAL",
        ):
            self._db.execute(f"PRAGMA {pragma}")
        migrate(self._db)
        self._embedder = embedder
        self._lock = asyncio.Lock()

    def close(self) -> None:
        self._db.close()

    async def _run[T](self, fn: Callable[[], T]) -> T:
        async with self._lock:
            return await asyncio.to_thread(fn)

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        # IMMEDIATE takes the write lock up front, so two writers queue instead of deadlocking.
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield self._db
            self._db.execute("COMMIT")
        except BaseException:
            self._db.execute("ROLLBACK")
            raise

    async def _embed(self, text: str) -> bytes:
        (vector,) = await asyncio.to_thread(self._embedder.embed, [text])
        return np.asarray(vector, dtype=np.float32).tobytes()

    def _all(self, sql: str, params: Any = ()) -> list[Any]:
        return self._db.execute(sql, params).fetchall()

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
        content_hash = turn_hash(session_id, occurred_at, user_text, assistant_text)

        def go() -> tuple[int, bool]:
            with self._tx() as db:
                row = db.execute(
                    """INSERT INTO turns (user_id, session_id, user_text, assistant_text,
                                          occurred_at, created_at, content_hash, namespace)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT (user_id, content_hash) DO NOTHING RETURNING id""",
                    (
                        user_id,
                        session_id,
                        user_text,
                        assistant_text,
                        _ts(occurred_at),
                        _ts(datetime.now(UTC)),
                        content_hash,
                        namespace,
                    ),
                ).fetchone()
                if row:
                    return int(row[0]), True
                row = db.execute(
                    "SELECT id FROM turns WHERE user_id = ? AND content_hash = ?",
                    (user_id, content_hash),
                ).fetchone()
                return int(row[0]), False

        return await self._run(go)

    async def get_turns(self, user_id: str, turn_ids: Sequence[int]) -> list[Turn]:
        rows = await self._run(
            lambda: self._all(
                f"""SELECT {TURN_COLUMNS} FROM turns
                WHERE user_id = ? AND id IN (SELECT value FROM json_each(?))""",  # noqa: S608
                (user_id, json.dumps(list(turn_ids))),
            )
        )
        by_id = {t.id: t for t in map(_turn, rows)}
        return [by_id[i] for i in turn_ids if i in by_id]

    async def turn_processed(self, user_id: str, turn_id: int) -> bool:
        rows = await self._run(
            lambda: self._all(
                "SELECT processed_at IS NOT NULL FROM turns WHERE user_id = ? AND id = ?",
                (user_id, turn_id),
            )
        )
        return bool(rows and rows[0][0])

    async def pending_users(self, limit: int = 100) -> list[str]:
        rows = await self._run(
            lambda: self._all(
                """SELECT DISTINCT user_id FROM turns
               WHERE processed_at IS NULL AND attempts < ? LIMIT ?""",
                (MAX_ATTEMPTS, limit),
            )
        )
        return [r[0] for r in rows]

    async def pending_turns(self, user_id: str, limit: int = 20) -> list[Turn]:
        rows = await self._run(
            lambda: self._all(
                f"""SELECT {TURN_COLUMNS} FROM turns
                WHERE user_id = ? AND processed_at IS NULL AND attempts < ?
                ORDER BY occurred_at, id LIMIT ?""",  # noqa: S608
                (user_id, MAX_ATTEMPTS, limit),
            )
        )
        return [_turn(r) for r in rows]

    async def finish_turn(self, turn_id: int, now: datetime, error: str | None = None) -> None:
        def go() -> None:
            with self._tx() as db:
                if error is None:
                    db.execute(
                        "UPDATE turns SET processed_at = ?, last_error = NULL WHERE id = ?",
                        (_ts(now), turn_id),
                    )
                else:
                    db.execute(
                        "UPDATE turns SET attempts = attempts + 1, last_error = ? WHERE id = ?",
                        (error[:1000], turn_id),
                    )

        await self._run(go)

    async def claim(self, user_id: str, holder: str, now: datetime, seconds: float) -> bool:
        def go() -> bool:
            with self._tx() as db:
                row = db.execute(
                    """INSERT INTO ingest_leases (user_id, holder, expires_at) VALUES (?, ?, ?)
                       ON CONFLICT (user_id) DO UPDATE
                           SET holder = excluded.holder, expires_at = excluded.expires_at
                           WHERE ingest_leases.expires_at < ?
                              OR ingest_leases.holder = excluded.holder
                       RETURNING user_id""",
                    (user_id, holder, _ts(now + timedelta(seconds=seconds)), _ts(now)),
                ).fetchone()
                return row is not None

        return await self._run(go)

    async def release(self, user_id: str, holder: str) -> None:
        def go() -> None:
            with self._tx() as db:
                db.execute(
                    "DELETE FROM ingest_leases WHERE user_id = ? AND holder = ?", (user_id, holder)
                )

        await self._run(go)

    async def session_turns(self, user_id: str, session_id: str, after_id: int = 0) -> list[Turn]:
        rows = await self._run(
            lambda: self._all(
                f"""SELECT {TURN_COLUMNS} FROM turns
                WHERE user_id = ? AND session_id = ? AND id > ?
                ORDER BY occurred_at, id""",  # noqa: S608
                (user_id, session_id, after_id),
            )
        )
        return [_turn(r) for r in rows]

    # ---- facts ----

    def _insert(
        self,
        db: sqlite3.Connection,
        user_id: str,
        content: str,
        source_turn_ids: Sequence[int],
        valid_from: datetime,
        embedding: bytes,
        namespace: str | None = None,
        kind: Kind = "fact",
        session_id: str | None = None,
    ) -> int:
        row = db.execute(
            """INSERT INTO memories (user_id, content, source_turn_ids, valid_from, created_at,
                                     embedding, embedding_model, namespace, kind, session_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id""",
            (
                user_id,
                content,
                json.dumps(list(source_turn_ids)),
                _ts(valid_from),
                _ts(datetime.now(UTC)),
                embedding,
                self._embedder.model_name,
                namespace,
                kind,
                session_id,
            ),
        ).fetchone()
        return int(row[0])

    async def add(
        self,
        user_id: str,
        content: str,
        source_turn_ids: Sequence[int],
        valid_from: datetime,
        namespace: str | None = None,
    ) -> int:
        embedding = await self._embed(content)

        def go() -> int:
            with self._tx() as db:
                return self._insert(
                    db, user_id, content, source_turn_ids, valid_from, embedding, namespace
                )

        return await self._run(go)

    async def get(self, user_id: str, memory_id: int) -> Memory | None:
        rows = await self._run(
            lambda: self._all(
                f"SELECT {COLUMNS} FROM memories WHERE id = ? AND user_id = ?",  # noqa: S608
                (memory_id, user_id),
            )
        )
        return _memory(rows[0]) if rows else None

    def _close(
        self,
        db: sqlite3.Connection,
        user_id: str,
        memory_id: int,
        at: datetime,
        superseded_by: int | None,
    ) -> bool:
        # max(): a late turn older than the fact closes it at its own start (see PostgresStore).
        cur = db.execute(
            """UPDATE memories SET valid_to = max(?, valid_from), superseded_by = ?
               WHERE id = ? AND user_id = ? AND valid_to IS NULL AND kind = 'fact'""",
            (_ts(at), superseded_by, memory_id, user_id),
        )
        return cur.rowcount == 1

    async def invalidate(
        self, user_id: str, memory_id: int, at: datetime, superseded_by: int | None = None
    ) -> bool:
        def go() -> bool:
            with self._tx() as db:
                return self._close(db, user_id, memory_id, at, superseded_by)

        return await self._run(go)

    async def supersede(
        self,
        user_id: str,
        old_id: int,
        content: str,
        source_turn_ids: Sequence[int],
        valid_from: datetime,
        namespace: str | None = None,
    ) -> int:
        embedding = await self._embed(content)

        def go() -> int:
            with self._tx() as db:
                new_id = self._insert(
                    db, user_id, content, source_turn_ids, valid_from, embedding, namespace
                )
                if not self._close(db, user_id, old_id, valid_from, new_id):
                    raise LookupError(f"memory {old_id} is not a current fact of {user_id}")
                return new_id

        return await self._run(go)

    async def history(self, user_id: str, memory_id: int) -> list[Memory]:
        rows = await self._run(
            lambda: self._all(
                f"""{CHAIN} SELECT {COLUMNS} FROM memories
                WHERE id IN (SELECT id FROM chain) ORDER BY valid_from, id""",  # noqa: S608
                {"u": user_id, "ids": json.dumps([memory_id])},
            )
        )
        return [_memory(r) for r in rows]

    async def drop(self, user_id: str, memory_id: int) -> bool:
        def go() -> bool:
            with self._tx() as db:
                cur = db.execute(
                    "DELETE FROM memories WHERE id = ? AND user_id = ?", (memory_id, user_id)
                )
                return cur.rowcount == 1

        return await self._run(go)

    async def forget(
        self, user_id: str, memory_ids: Sequence[int] | None = None
    ) -> tuple[int, int]:
        """Hard delete, with the same rules as PostgresStore.forget."""

        def go() -> tuple[int, int]:
            with self._tx() as db:
                db.execute(
                    "DELETE FROM memories WHERE user_id = ? AND kind = 'profile'", (user_id,)
                )
                if memory_ids is None:
                    mems = db.execute("DELETE FROM memories WHERE user_id = ?", (user_id,)).rowcount
                    turns = db.execute("DELETE FROM turns WHERE user_id = ?", (user_id,)).rowcount
                else:
                    params = {"u": user_id, "ids": json.dumps(list(memory_ids))}
                    # Collect first, then delete: the chain must not shift under its own delete.
                    chain_sql = f"{CHAIN} SELECT id FROM chain"  # noqa: S608
                    chain = [r[0] for r in db.execute(chain_sql, params)]
                    turn_ids = [
                        r[0]
                        for r in db.execute(
                            """SELECT DISTINCT j.value
                               FROM memories m, json_each(m.source_turn_ids) j
                               WHERE m.id IN (SELECT value FROM json_each(?))""",
                            (json.dumps(chain),),
                        )
                    ]
                    mems = db.execute(
                        """DELETE FROM memories WHERE user_id = ?
                           AND (id IN (SELECT value FROM json_each(?))
                                OR EXISTS (SELECT 1 FROM json_each(memories.source_turn_ids) j
                                           WHERE j.value IN (SELECT value FROM json_each(?))))""",
                        (user_id, json.dumps(chain), json.dumps(turn_ids)),
                    ).rowcount
                    turns = db.execute(
                        """DELETE FROM turns
                           WHERE user_id = ? AND id IN (SELECT value FROM json_each(?))""",
                        (user_id, json.dumps(turn_ids)),
                    ).rowcount
                db.execute(
                    """INSERT INTO deletions (user_id, deleted_at, memories, turns)
                       VALUES (?, ?, ?, ?)""",
                    (user_id, _ts(datetime.now(UTC)), mems, turns),
                )
                return mems, turns

        return await self._run(go)

    # ---- ranking (ADR 0016) ----

    async def touch(
        self, user_id: str, memory_ids: Sequence[int], at: datetime, rank: Rank
    ) -> None:
        if not memory_ids:
            return

        def go() -> None:
            with self._tx() as db:
                db.execute(
                    """UPDATE memories
                       SET importance = min(importance + ?, ?), last_accessed_at = ?
                       WHERE user_id = ? AND id IN (SELECT value FROM json_each(?))""",
                    (
                        rank.access_boost,
                        rank.importance_cap,
                        _ts(at),
                        user_id,
                        json.dumps(list(memory_ids)),
                    ),
                )

        await self._run(go)

    async def unarchive(self, user_id: str, memory_ids: Sequence[int]) -> int:
        def go() -> int:
            with self._tx() as db:
                return db.execute(
                    """UPDATE memories SET archived_at = NULL
                       WHERE user_id = ? AND id IN (SELECT value FROM json_each(?))
                         AND archived_at IS NOT NULL""",
                    (user_id, json.dumps(list(memory_ids))),
                ).rowcount

        return await self._run(go)

    async def _rerank(
        self, ids: list[int], scores: dict[int, float], rank: Rank, now: datetime
    ) -> list[int]:
        rows = await self._run(
            lambda: self._all(
                """SELECT id, importance, coalesce(last_accessed_at, valid_from) FROM memories
               WHERE id IN (SELECT value FROM json_each(?))""",
                (json.dumps(ids),),
            )
        )
        adjusted = {}
        for i, importance, touched in rows:
            age = (now - datetime.fromisoformat(touched)).total_seconds() / 86400
            adjusted[i] = scores[i] * rank.factor(float(importance), age)
        return sorted(ids, key=adjusted.__getitem__, reverse=True)

    # ---- search ----

    @staticmethod
    def _scope(
        user_id: str, as_of: datetime | None, namespace: str | None, kinds: Sequence[Kind]
    ) -> dict[str, Any]:
        return {
            "user_id": user_id,
            "as_of": _ts(as_of) if as_of else None,
            "ns": namespace,
            "kinds": json.dumps(list(kinds)),
        }

    async def search_vector(
        self,
        user_id: str,
        query: str,
        limit: int = 20,
        as_of: datetime | None = None,
        namespace: str | None = None,
        kinds: Sequence[Kind] = SEARCHABLE,
    ) -> list[int]:
        q = np.frombuffer(await self._embed(query), dtype=np.float32)
        params = self._scope(user_id, as_of, namespace, kinds)
        sql = f"SELECT id, embedding FROM memories WHERE {VISIBLE} AND embedding IS NOT NULL"  # noqa: S608
        rows = await self._run(lambda: self._all(sql, params))
        if not rows:
            return []
        # ponytail: exact scan of one user's vectors, ~1 ms per 10k facts. Past ~100k facts
        # per user, swap in sqlite-vec or move that user to Postgres (ADR 0019).
        matrix = np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float32).reshape(-1, DIM)
        best = np.argsort(-(matrix @ q), kind="stable")[:limit]
        return [int(rows[i][0]) for i in best]

    async def search_text(
        self,
        user_id: str,
        query: str,
        limit: int = 20,
        as_of: datetime | None = None,
        namespace: str | None = None,
        kinds: Sequence[Kind] = SEARCHABLE,
    ) -> list[int]:
        queries = _fts_queries(query)
        if queries is None:
            return []
        params = self._scope(user_id, as_of, namespace, kinds) | {"limit": limit}

        def go() -> list[int]:
            for match in queries:
                rows = self._all(
                    f"""SELECT m.id FROM memories_fts f JOIN memories m ON m.id = f.rowid
                        WHERE memories_fts MATCH :match AND {VISIBLE}
                        ORDER BY bm25(memories_fts), m.id LIMIT :limit""",  # noqa: S608
                    params | {"match": match},
                )
                if rows:
                    return [r[0] for r in rows]
            return []

        return await self._run(go)

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
        rankings = []
        if retriever in ("hybrid", "vector"):
            rankings.append(await self.search_vector(user_id, query, 20, as_of, namespace, kinds))
        if retriever in ("hybrid", "text"):
            rankings.append(await self.search_text(user_id, query, 20, as_of, namespace, kinds))
        scores = rrf_scores(rankings)
        ids = sorted(scores, key=scores.__getitem__, reverse=True)
        if rank is not None and ids:
            ids = await self._rerank(ids, scores, rank, now or datetime.now(UTC))
        ids = ids[:limit]
        rows = await self._run(
            lambda: self._all(
                f"SELECT {COLUMNS} FROM memories WHERE id IN (SELECT value FROM json_each(?))",  # noqa: S608
                (json.dumps(ids),),
            )
        )
        by_id = {m.id: m for m in map(_memory, rows)}
        return [by_id[i] for i in ids]

    # ---- episodes and profiles (ADR 0021) ----

    def _derived(
        self, user_id: str, kind: Kind, namespace: str | None, session_id: str | None
    ) -> Memory | None:
        if kind == "episode":
            rows = self._all(
                f"""SELECT {COLUMNS} FROM memories
                    WHERE user_id = ? AND kind = 'episode' AND session_id = ?""",  # noqa: S608
                (user_id, session_id),
            )
        else:
            rows = self._all(
                f"""SELECT {COLUMNS} FROM memories
                    WHERE user_id = ? AND kind = ? AND namespace IS ?""",  # noqa: S608
                (user_id, kind, namespace),
            )
        return _memory(rows[0]) if rows else None

    async def derived(
        self, user_id: str, kind: Kind, namespace: str | None = None, session_id: str | None = None
    ) -> Memory | None:
        return await self._run(lambda: self._derived(user_id, kind, namespace, session_id))

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
        embedding = await self._embed(content)

        def go() -> int:
            with self._tx() as db:
                existing = self._derived(user_id, kind, namespace, session_id)
                if existing is None:
                    return self._insert(
                        db,
                        user_id,
                        content,
                        source_turn_ids,
                        valid_from,
                        embedding,
                        namespace,
                        kind,
                        session_id,
                    )
                db.execute(
                    """UPDATE memories SET content = ?, source_turn_ids = ?, embedding = ?
                       WHERE id = ?""",
                    (content, json.dumps(list(source_turn_ids)), embedding, existing.id),
                )
                return existing.id

        return await self._run(go)

    async def current_facts(
        self, user_id: str, namespace: str | None, limit: int = 200
    ) -> list[Memory]:
        rows = await self._run(
            lambda: self._all(
                f"""SELECT {COLUMNS} FROM memories
                WHERE user_id = ? AND kind = 'fact' AND valid_to IS NULL
                  AND archived_at IS NULL AND namespace IS ?
                ORDER BY importance DESC, valid_from DESC, id DESC LIMIT ?""",  # noqa: S608
                (user_id, namespace, limit),
            )
        )
        return [_memory(r) for r in rows]

    async def recent_episodes(
        self, user_id: str, namespace: str | None, limit: int = 3
    ) -> list[Memory]:
        rows = await self._run(
            lambda: self._all(
                f"""SELECT {COLUMNS} FROM memories
                WHERE user_id = ? AND kind = 'episode' AND (? IS NULL OR namespace = ?)
                ORDER BY valid_from DESC, id DESC LIMIT ?""",  # noqa: S608
                (user_id, namespace, namespace, limit),
            )
        )
        return [_memory(r) for r in rows]

    # ---- tenant keys (ADR 0023) ----

    async def add_api_key(self, tenant: str, key_sha256: str) -> None:
        def go() -> None:
            with self._tx() as db:
                db.execute(
                    "INSERT INTO api_keys (tenant, key_sha256, created_at) VALUES (?, ?, ?)",
                    (tenant, key_sha256, _ts(datetime.now(UTC))),
                )

        await self._run(go)

    async def tenant_for_key(self, key_sha256: str) -> str | None:
        rows = await self._run(
            lambda: self._all(
                "SELECT tenant FROM api_keys WHERE key_sha256 = ? AND revoked_at IS NULL",
                (key_sha256,),
            )
        )
        return str(rows[0][0]) if rows else None

    async def revoke_api_keys(self, tenant: str, now: datetime) -> int:
        def go() -> int:
            with self._tx() as db:
                return db.execute(
                    "UPDATE api_keys SET revoked_at = ? WHERE tenant = ? AND revoked_at IS NULL",
                    (_ts(now), tenant),
                ).rowcount

        return await self._run(go)

    async def stats(self, user_id: str) -> dict[str, object]:
        rows = await self._run(lambda: self._all(STATS_SQL.format(p=":u"), {"u": user_id}))
        return dict(zip(STATS_KEYS, rows[0], strict=True))
