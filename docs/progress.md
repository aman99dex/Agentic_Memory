# Progress log

Newest entry first. Every session ends by adding one entry here.

## Current status
- **Phase:** 1 merged. **Phase 2 done and on `main`**: the write path works end to end
  (`write_path.py`: extract -> reconcile -> apply, idempotent `add_turn`, crash-safe retry).
  Verified with real Postgres + real Haiku 5.5: `tests/test_write_path.py` (2 tests) and a
  seven-turn run over a fictional user's life; 20 tests pass, ruff/mypy clean.
- **Workflow (Aman, 2026-10-26): build everything end to end first, teach last.** No lessons
  or check questions while building; concepts go into `docs/learn/phase-N.md`.

## Next session: start here
1. Phase 3: the read path (query rewrite, time-range filter via `as_of`, context packing that
   returns the source turns, per ADR 0003). See `plan.md` Phase 3.
2. Then Phase 4 (FastAPI + MCP) so the whole thing runs end to end as a service.
3. Append each concept to `docs/learn/phase-N.md` as it lands (`phase-3.md` does not exist yet).
4. Teaching pass once it works end to end. Pending checks: see the `<details>` in
   `docs/learn/phase-2.md`.
5. Known gaps to consider: turns must be ingested in time order (CHECK on `valid_to >=
   valid_from`); the write path has no concurrency guard across two simultaneous turns of
   one user (two near-identical facts could both ADD).

## Open questions
- None. LLM: Aman's personal Anthropic key is in `.env` (gitignored, verified working with a
  1-token call), Haiku 5.5 for every role (ADR 0012). Judge for Phase 5 still pinned (ADR 0007).

---

## 2026-10-26: Phase 2, the write path works
**Done**
- Committed and pushed Phase 2 part 1 (`9d32da7`) straight to `main` (owner bypass works).
- Added langgraph 1.2.17, langchain 1.4.7, langchain-anthropic 1.7.6; verified
  `init_chat_model`, `StateGraph.add_conditional_edges`, `with_structured_output` signatures
  in the installed packages.
- `write_path.py`: `extract` (structured `Facts`), `reconcile` (top-5 hybrid search +
  `Decision`, target guarded by `_check`), `apply` (add / supersede / invalidate), two
  conditional edges, `ingest_turn`. `store.turn_has_memories` for crash-safe retry.
- ADR 0013. `docs/learn/phase-2.md` (sections 2.0-2.7). Plan Phase 2 boxes ticked.
- Ran 7 turns of a fictional user through Haiku 5.5 and fixed 4 prompt flaws (see
  `docs/learn/phase-2.md` 2.7).

**Learned / surprising**
- `claude-haiku-5-5` rejects `temperature` (HTTP 400), so `make_llm` sends none. Anthropic API
  behaviour, not a LangChain bug.
- `uv run pytest` fails here ("uv trampoline failed to canonicalize script path"); use
  `uv run --env-file .env python -m pytest`.
- `INVALIDATE` stores nothing, so a replacement fact labelled INVALIDATE is lost. The
  prompt now says UPDATE for "new truth replaces old".

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
