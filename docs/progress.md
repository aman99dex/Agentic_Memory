# Progress log

Newest entry first. Every session ends by adding one entry here.

## Current status
- **Phase:** 1 code complete on branch `phase-1-storage` (pushed). 17 tests, CI checks green
  locally. **Next: open the PR for Phase 1, then start Phase 2 (the LangGraph write path).**
- Lessons 1.1–1.11 are written up in `docs/learn/phase-1.md`.

## Next session: start here
1. Ask Aman whether to open the Phase 1 PR (`gh pr create` from `phase-1-storage`) and
   check CI on GitHub. Merge only when Aman says.
2. Phase 2, branch `phase-2-write-path`. Begin with the big picture of the write path,
   then Lesson 2.1. Needs an LLM provider/key decision first (open question below);
   never the Neoflo/Collections key.
3. Phase 2 also needs a `turns` side in `MemoryStore` (`add_turn`) and a
   `0002_*.sql` migration for anything new (0001 is applied and checksummed: don't edit it).

## Open questions
- Which LLM provider and key for the personal project? Needed at the start of Phase 2 (ADR 0007).

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
