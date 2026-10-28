"""Nightly forgetting (ADR 0016): decay importance, then archive cold, unimportant facts.

Archived facts are hidden from search, never deleted (`MemoryStore.unarchive` brings them back).
Run it on a schedule, e.g. a Cloud Scheduler call to a Cloud Run job:
    DATABASE_URL=... uv run python -m agentic_memory.decay
"""

import os
from datetime import UTC, datetime, timedelta

import psycopg

DECAY = 0.9  # importance is multiplied by this every night
FLOOR = 0.05  # at or below this, importance counts as "low"
COLD_DAYS = 365  # not recalled for this long counts as "cold"


def decay(
    conn: psycopg.Connection,
    now: datetime,
    factor: float = DECAY,
    floor: float = FLOOR,
    cold_days: int = COLD_DAYS,
) -> tuple[int, int]:
    """Returns (facts whose importance decayed, facts archived). `conn` must be autocommit."""
    with conn.transaction():
        decayed = conn.execute(
            "UPDATE memories SET importance = importance * %s WHERE importance > 0", (factor,)
        ).rowcount
        # Only current facts are archived: a closed one is history, already out of search.
        archived = conn.execute(
            """UPDATE memories SET archived_at = %s
               WHERE archived_at IS NULL AND valid_to IS NULL AND importance <= %s
                 AND coalesce(last_accessed_at, created_at) < %s""",
            (now, floor, now - timedelta(days=cold_days)),
        ).rowcount
    return decayed, archived


if __name__ == "__main__":
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        decayed, archived = decay(conn, datetime.now(UTC))
    print(f"decayed {decayed} facts, archived {archived}")
