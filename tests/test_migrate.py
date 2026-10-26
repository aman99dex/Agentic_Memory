import uuid

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from agentic_memory.migrate import MIGRATIONS, migrate


@pytest.fixture
def fresh(db_url):
    """An empty database of its own, so these tests start from nothing."""
    name = f"m_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(db_url, autocommit=True) as admin:
        admin.execute(f"CREATE DATABASE {name}")
    with psycopg.connect(make_conninfo(db_url, dbname=name), autocommit=True) as c:
        yield c


def tables(conn):
    rows = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'").fetchall()
    return {r[0] for r in rows}


def test_real_migrations_apply_once(fresh):
    assert migrate(fresh) == sorted(p.name for p in MIGRATIONS.glob("*.sql"))
    assert {"turns", "memories", "schema_migrations"} <= tables(fresh)
    assert migrate(fresh) == []  # second run: nothing to do


def test_a_broken_file_leaves_no_trace(fresh, tmp_path):
    (tmp_path / "0001_ok.sql").write_text("CREATE TABLE a (x int);")
    (tmp_path / "0002_broken.sql").write_text("CREATE TABLE b (x int); SELEKT oops;")
    with pytest.raises(psycopg.errors.SyntaxError):
        migrate(fresh, tmp_path)
    assert "a" in tables(fresh)
    assert "b" not in tables(fresh)  # rolled back with the rest of 0002
    names = fresh.execute("SELECT name FROM schema_migrations").fetchall()
    assert names == [("0001_ok.sql",)]


def test_editing_an_applied_file_is_refused(fresh, tmp_path):
    f = tmp_path / "0001_ok.sql"
    f.write_text("CREATE TABLE a (x int);")
    migrate(fresh, tmp_path)
    f.write_text("CREATE TABLE a (x bigint);")
    with pytest.raises(RuntimeError, match="changed after it was applied"):
        migrate(fresh, tmp_path)
