# 0019. SQLite is the local default; Postgres stays for servers

**Status:** Accepted, 2026-11-04. Phase 7 (`docs/intent/usable-product.md`). Extends ADR 0001
(one Postgres store) to two stores behind one interface.

## Context
A real session on 2026-11-04 showed the biggest barrier to anyone using this: five setup steps,
including Docker and a Postgres that must keep running. For one developer on a laptop that is
too much, when Claude Code's built-in memory needs zero. Products, on the other hand, already run
Postgres, and managed Postgres (Supabase, Neon) ships pgvector.

## Options
1. **Postgres only** (as before). Right for servers, wrong for a laptop.
2. **SQLite with sqlite-vec** for vectors. One file, but sqlite-vec is pre-1.0 (0.1.7), needs
   `load_extension`, which some Python builds disable (macOS system Python), and its KNN with a
   per-user filter is brute force anyway.
3. **SQLite with exact vector search in numpy.** Vectors are float32 BLOBs; a search loads the
   visible vectors of one user and takes a dot product. numpy is already installed (fastembed
   needs it). FTS5 gives keyword search with real BM25.
4. **An embedded vector database** (LanceDB, Chroma). A second storage engine to learn, and no
   transactions shared with the facts.

## Decision
- **Option 3, chosen by the URL.** No `DATABASE_URL`, or `sqlite:///path`, gives `SqliteStore`
  at `~/.agentic-memory/memory.db` (or `AGENTIC_MEMORY_HOME`). A `postgresql://` URL gives
  `PostgresStore`. Both implement one `Store` protocol (`store.py`), and the store tests run
  against both (`any_store` fixture): the two must behave the same.
- **Keyword search:** FTS5, Porter stemming, `bm25()` ranking, AND first then OR (ADR 0011). A
  short stopword list stands in for what Postgres' `english` config drops. Words are quoted, so
  input is never read as FTS syntax.
- **Times** are fixed-width ISO 8601 UTC text, so comparing the text compares the moments.
  `json_each` stands in for Postgres arrays.
- **Concurrency:** WAL mode, a 10 s busy timeout, `BEGIN IMMEDIATE` for every write, one
  connection per process used through an asyncio lock. A hook, the MCP server and a worker can
  share one file.
- **Migrations ship inside the package** (`agentic_memory/migrations/{postgres,sqlite}`), so an
  installed copy can create its own database. SQLite migrates itself when it opens, counting
  applied files in `PRAGMA user_version`. ADR 0010's rule (migrate as a deploy step, never at
  startup) still holds for servers: local mode has no deploy step.

## Trade-off
- Exact search reads every visible vector of one user per query. That is about 1 ms per 10,000
  facts, fine for one person. Past roughly 100,000 facts per user, swap in sqlite-vec or move
  that user to Postgres. The code says so where it matters (`ponytail:` comment).
- Two SQL dialects to keep in step. The shared test suite is the guard; a feature that only
  one store supports is a bug.
- SQLite allows one writer at a time. A server with many users belongs on Postgres.

**In an interview:** "Locally it's one SQLite file with BM25 and exact vector search, so the
install is one command; products point the same code at Postgres. Both stores pass the same
test suite, which is what keeps two dialects honest."
