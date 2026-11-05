"""What every store is: the shared types and the `Store` interface (ADRs 0019-0021).

`PostgresStore` (postgres_store.py) serves products and the evaluation; `SqliteStore`
(sqlite_store.py) is the zero-setup local default. This module imports neither database
driver, so a hook that only reads SQLite stays fast.
"""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from agentic_memory.rank import Rank

Retriever = Literal["hybrid", "vector", "text"]
# fact: one durable statement. episode: a rolling summary of one session. profile: a short
# portrait of the user (namespace NULL) or of one project (ADR 0021).
Kind = Literal["fact", "episode", "profile"]
SEARCHABLE: tuple[Kind, ...] = ("fact", "episode")  # profiles come back through `context`
MAX_ATTEMPTS = 5  # a turn that failed this often stops being retried (ADR 0020)


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
    namespace: str | None = None
    kind: str = "fact"


@dataclass(frozen=True)
class Turn:
    id: int
    session_id: str
    user_text: str
    assistant_text: str
    occurred_at: datetime
    namespace: str | None = None


def turn_hash(session_id: str, occurred_at: datetime, user_text: str, assistant_text: str) -> str:
    """The idempotency key of one round. JSON keeps field boundaries: ("ab","c") != ("a","bc")."""
    fingerprint = json.dumps([session_id, occurred_at.isoformat(), user_text, assistant_text])
    return hashlib.sha256(fingerprint.encode()).hexdigest()


class Store(Protocol):
    """Everything the write path, read path, worker and API need from a database."""

    async def add_turn(
        self,
        user_id: str,
        session_id: str,
        user_text: str,
        assistant_text: str,
        occurred_at: datetime,
        namespace: str | None = None,
    ) -> tuple[int, bool]: ...
    async def get_turns(self, user_id: str, turn_ids: Sequence[int]) -> list[Turn]: ...
    async def turn_processed(self, user_id: str, turn_id: int) -> bool: ...
    async def pending_users(self, limit: int = 100) -> list[str]: ...
    async def pending_turns(self, user_id: str, limit: int = 20) -> list[Turn]: ...
    async def finish_turn(self, turn_id: int, now: datetime, error: str | None = None) -> None: ...
    async def claim(self, user_id: str, holder: str, now: datetime, seconds: float) -> bool: ...
    async def release(self, user_id: str, holder: str) -> None: ...
    async def session_turns(
        self, user_id: str, session_id: str, after_id: int = 0
    ) -> list[Turn]: ...
    async def add(
        self,
        user_id: str,
        content: str,
        source_turn_ids: Sequence[int],
        valid_from: datetime,
        namespace: str | None = None,
    ) -> int: ...
    async def get(self, user_id: str, memory_id: int) -> Memory | None: ...
    async def invalidate(
        self, user_id: str, memory_id: int, at: datetime, superseded_by: int | None = None
    ) -> bool: ...
    async def supersede(
        self,
        user_id: str,
        old_id: int,
        content: str,
        source_turn_ids: Sequence[int],
        valid_from: datetime,
        namespace: str | None = None,
    ) -> int: ...
    async def drop(self, user_id: str, memory_id: int) -> bool: ...
    async def history(self, user_id: str, memory_id: int) -> list[Memory]: ...
    async def forget(
        self, user_id: str, memory_ids: Sequence[int] | None = None
    ) -> tuple[int, int]: ...
    async def touch(
        self, user_id: str, memory_ids: Sequence[int], at: datetime, rank: Rank
    ) -> None: ...
    async def unarchive(self, user_id: str, memory_ids: Sequence[int]) -> int: ...
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
    ) -> list[Memory]: ...
    async def derived(
        self, user_id: str, kind: Kind, namespace: str | None = None, session_id: str | None = None
    ) -> Memory | None: ...
    async def put_derived(
        self,
        user_id: str,
        kind: Kind,
        content: str,
        source_turn_ids: Sequence[int],
        valid_from: datetime,
        namespace: str | None = None,
        session_id: str | None = None,
    ) -> int: ...
    async def current_facts(
        self, user_id: str, namespace: str | None, limit: int = 200
    ) -> list[Memory]: ...
    async def recent_episodes(
        self, user_id: str, namespace: str | None, limit: int = 3
    ) -> list[Memory]: ...
    async def add_api_key(self, tenant: str, key_sha256: str) -> None: ...
    async def tenant_for_key(self, key_sha256: str) -> str | None: ...
    async def revoke_api_keys(self, tenant: str, now: datetime) -> int: ...
    async def stats(self, user_id: str) -> dict[str, object]: ...


# What `agentic-memory status` shows. Plain SQL both databases speak; {p} is the placeholder.
STATS_SQL = f"""SELECT
    (SELECT count(*) FROM memories WHERE user_id = {{p}} AND kind = 'fact'
        AND valid_to IS NULL AND archived_at IS NULL),
    (SELECT count(*) FROM memories WHERE user_id = {{p}} AND kind = 'fact'
        AND valid_to IS NOT NULL),
    (SELECT count(*) FROM memories WHERE user_id = {{p}} AND kind = 'episode'),
    (SELECT count(*) FROM memories WHERE user_id = {{p}} AND kind = 'profile'),
    (SELECT count(*) FROM turns WHERE user_id = {{p}}),
    (SELECT count(*) FROM turns WHERE user_id = {{p}} AND processed_at IS NULL
        AND attempts < {MAX_ATTEMPTS}),
    (SELECT count(*) FROM turns WHERE user_id = {{p}} AND processed_at IS NULL
        AND attempts >= {MAX_ATTEMPTS}),
    (SELECT last_error FROM turns WHERE user_id = {{p}} AND last_error IS NOT NULL
        ORDER BY id DESC LIMIT 1)"""  # noqa: S608 - only a constant is glued in
STATS_KEYS = (
    "current facts",
    "closed facts",
    "sessions",
    "profiles",
    "turns",
    "queued",
    "failed",
    "last error",
)
