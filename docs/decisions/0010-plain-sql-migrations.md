# 0010. Plain SQL migrations, no ORM

**Status:** Accepted, 2026-10-16. **Changes the original plan (Alembic).**

## Context
The hot queries are pgvector- and full-text-specific: `<=>`, `SET LOCAL
hnsw.iterative_scan`, `ts_rank_cd`, and RRF in a CTE. An ORM adds nothing to them and
gets in the way of reading them. Alembic's main features, autogenerate and downgrades,
only pay off with ORM models.

## Decision
- psycopg 3 (async) with a connection pool, hand-written SQL.
- `migrations/NNNN_name.sql` files, applied in order by a short runner that records them
  in a `schema_migrations` table inside one transaction per file.
- Migrations are **forward-only**: a fix is a new migration. Migrations run as a separate
  deploy step, never at app startup, so two instances can't race.

**Revisit if** the schema gets many tables with simple CRUD, where an ORM saves real code.

**In an interview:** "My queries are vector-SQL that an ORM would hide, so I wrote SQL
directly. The migrations are forward-only and run as their own deploy step."
