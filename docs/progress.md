# Progress log

Newest entry first. Every session ends by adding one entry here.

## Current status
- **Phase:** 1 merged to `main` (PR #2). Phase 2 started on `phase-2-write-path` (cut from
  the new main). **Uncommitted** work: ADR 0012, `migrations/0002_turn_content_hash.sql`,
  `MemoryStore.add_turn`, its test, and a `test_migrate.py` fix. 18 tests pass, CI checks clean.
- Aman wants to **restart Phase 2 from the top** next session (re-teach the big picture
  and Lesson 2.1 before moving on).

## Next session: start here
**2026-10-26: Aman changed the order: build the whole thing end to end first, teach last.**
No lessons or check questions while building; keep notes in `docs/learn/phase-N.md`.
1. Build the Phase 2 graph: `extract` node, then `reconcile` node, then the action step
   (ADD / UPDATE / INVALIDATE / NOOP). Verify LangGraph 1.x and `init_chat_model` APIs
   against current docs first; add the deps.
2. Then run the write path end to end on real data before moving on.
3. Write each concept into `docs/learn/phase-2.md` as it lands. Teaching starts once the
   system works end to end. Pending checks: Lesson 2.0 (which label for "Ravi still lives
   in Pune"? NOOP) and Lesson 2.1 ("ok thanks" twice in `s1` at 10:00 and 10:05: two rows,
   because occurred_at differs).

## Open questions
- None. LLM: Aman's personal Anthropic key is in `.env` (gitignored, verified working with a
  1-token call), Haiku 5.5 for every role (ADR 0012). Judge for Phase 5 still pinned (ADR 0007).

---

## 2026-10-18: Phase 2, part 1
**Done**
- Opened and merged the Phase 1 PR (#2). `main` has a ruleset that blocks updates except
  for bypass actors, so Aman merges.
- ADR 0012: Haiku 5.5 for every LLM role during development.
- `.env` with `ANTHROPIC_API_KEY` and the three model vars.
- Lesson 2.0 (write-path big picture) and Lesson 2.1 (idempotent `add_turn`).

**Learned / surprising**
- A single INSERT ... ON CONFLICT + SELECT statement can miss a concurrent winner's row
  (one snapshot per statement), so the lookup is a second statement.
- Aman first answered "add_turn" to the reconcile question; the contacts-app table fixed it.

---

## 2026-10-18: Phase 1, part 2
**Done**
- Lesson 1.7: `tsv` generated columns + GIN indexes on both tables.
- Lesson 1.8: `fusion.rrf` (k=60) + `tests/test_rrf.py`. Aman asked to see the answer
  rather than write it; walked through it line by line.
- Lesson 1.9: `MemoryStore` (`store.py`): add / get / invalidate / supersede (one
  transaction) / search_vector (iterative scan, MATERIALIZED re-sort) / search_text /
  hybrid search (gather + RRF) / `as_of`. `tests/test_store.py`, 6 tests.
- ADR 0011: keyword query AND first, OR fallback.
- Lesson 1.10: async + pool, measured (new conn 43 ms vs pool 4 ms; gather 0.42 s vs 2.1 s).
- Lesson 1.11: `migrate.py` runner with SHA-256 checksums; conftest uses it;
  `tests/test_migrate.py`, 3 tests. Local DB reset and migrated with the runner.
- `docs/learn/phase-1.md` written.

**Learned / surprising**
- OR-semantics keyword search made RRF tie the right ID with a wrong one (no IDF in
  `ts_rank_cd`). Strong motivation for the Phase 5 BM25 ablation.
- psycopg async needs `SelectorEventLoop` on Windows; pytest-asyncio 1.4 sets it through
  the `pytest_asyncio_loop_factories` hook.
- Aman missed the pool-waves question first time; the elevator analogy worked. Aman also
  didn't know why migrations are zero-padded (text vs number sort).

## 2026-10-18: Phase 1, part 1
**Done**
- `migrations/0001_init.sql`: `turns` (occurred_at + created_at), `memories`
  (valid_from/valid_to/superseded_by, source_turn_ids), `vector(384)` + `embedding_model`
  on both, HNSW (m=16, ef_construction=64) with cosine ops.
- `Embedder` over fastembed 0.9.0 / bge-small (384 dims, unit-length vectors).
- `tests/conftest.py` applies all migrations to a testcontainer;
  `tests/test_filtered_hnsw.py` proves 100 users x 50 memories returns 0 of 10 rows
  without iterative scan and 10 with it. 4/4 tests pass.

**Learned / surprising**
- fastembed's `query_embed` for bge-small adds no query instruction; BGE v1.5 says it is
  optional. Try the prefix as a Phase 5 ablation.
- Short facts that all start with "Aman ..." sit close together (cosine distance 0.49
  vs 0.55), so vector-only ranking margins are thin. One more reason for hybrid search.

## Open questions
- Which LLM provider and key for the personal project? Not needed until Phase 2 (ADR 0007).

---

## 2026-10-16: Phase 0
**Done**
- Research: memory systems (Mem0, Zep/Graphiti, Letta, Mastra…), the LongMemEval and
  LoCoMo details, current stack versions, and A/B and deployment options.
- Wrote ADRs 0001–0010. The plan is revised; the main change is ADR 0003 (facts as
  keys, turns as values).
- Moved the course material to `archive/course-notebooks/` with credits.
- Scaffold: a uv project, ruff, strict mypy, GitHub Actions CI, and docker-compose
  running pgvector 0.8.7 on Postgres 18.
- Smoke tests: pgvector ≥ 0.8.2, the iterative-scan setting exists, and tsvector
  stemming works. 2/2 pass.

**Learned / surprising**
- Neon retired pg_search on 2026-09-21, so true BM25 can't be assumed on managed
  Postgres.
- Haiku 5.5 at $0.10 per million input tokens makes full-benchmark ingestion about $20.
- `testcontainers.postgres` is deprecated; the import is now
  `testcontainers.community.postgres`.
