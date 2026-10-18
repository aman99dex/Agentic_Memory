"""Apply migrations/NNNN_name.sql in order, each once, each in its own transaction (ADR 0010).

Run it as a deploy step, never at app startup:
    DATABASE_URL=... uv run python -m agentic_memory.migrate
"""

import hashlib
import os
from pathlib import Path

import psycopg

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"


def migrate(conn: psycopg.Connection, directory: Path = MIGRATIONS) -> list[str]:
    """Returns the names of the files it applied. `conn` must be in autocommit mode."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
               name       text PRIMARY KEY,
               sha256     text NOT NULL,
               applied_at timestamptz NOT NULL DEFAULT now())"""
    )
    done: dict[str, str] = dict(
        conn.execute("SELECT name, sha256 FROM schema_migrations").fetchall()
    )
    applied = []
    for path in sorted(directory.glob("*.sql")):
        sql = path.read_text(encoding="utf-8")
        digest = hashlib.sha256(sql.encode()).hexdigest()
        if path.name in done:
            # Forward-only: a fix is a new file. Editing an applied one would split environments.
            if done[path.name] != digest:
                raise RuntimeError(f"{path.name} changed after it was applied; add a new migration")
            continue
        with conn.transaction():  # the file and its record land together, or neither does
            conn.execute(sql.encode())
            conn.execute(
                "INSERT INTO schema_migrations (name, sha256) VALUES (%s, %s)", (path.name, digest)
            )
        applied.append(path.name)
    return applied


if __name__ == "__main__":
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        names = migrate(conn)
    print("applied:", ", ".join(names) if names else "nothing, already up to date")
