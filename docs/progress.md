# Progress log

Newest entry first. Every session ends by adding one entry here.

## Current status
- **Phases 0-2 on `main`. Everything after is built, tested and UNCOMMITTED** (Phases 3-4, the
  Phase 5 harness, rerank/decay, SDK, telemetry, load test, Docker, deploy workflow, docs).
  The system works end to end: ingest a turn, it becomes versioned facts, recall returns
  current facts + their source turns under a token budget, over REST, MCP and a Python SDK,
  with hard delete, rerank by importance/recency and nightly archiving. Verified: 59 tests
  (real Postgres + real Haiku 5.5), ruff/mypy clean, the Docker image builds and the compose
  `migrate` + `api` services ran live (curl: auth, ingest, both search modes, importance rose).
- **What has NOT been run:** the real evaluation (only a 1-question smoke test exists), the
  ablations, LoCoMo through the LLMs, the locust load test, the CD workflow, the 50 hand labels
  for the judge. So the README Results block still says "No evaluation run yet", and no number
  in the plan's "Measured" column is filled in.
- **Workflow (Aman, 2026-10-26): build everything end to end first, teach last.** No lessons
  or check questions while building; concepts go into `docs/learn/phase-N.md`
  (phase-2 to 6 are written and waiting to be taught).
- Not built: the demo GIF (needs a human recording). Rerank and decay-forgetting were built
  after the harness (ADR 0016); the Phase 5 ablation decides whether rerank stays on.

## Next session: start here
1. Ask whether to commit + push (Aman's pattern: push to `main` directly), then tag `v0.1.0`
   only once real results are in the README.
2. **Run the evaluation, on an idle machine** (the first attempt made Aman's machine lag):
   `uv run --env-file .env python -m eval.lme --n 12 --name pilot-12 --concurrency 2`, then the
   ablations (`--retriever vector|text`, `--no-history`, `--no-rerank`, `--systems ours`), then
   `--dataset locomo`. Render with `python -m eval.report <names> --readme`. If `--no-rerank`
   does as well as the default, switch rerank off (ADR 0016 says the default must earn its place).
3. Hand-label 50 answers (`python -m eval.agreement sample <run>`, then `score`).
4. Load test (`docs/learn/phase-6.md` 6.2), then fill the plan's "Measured" table from JSON.
5. Cloud setup for `docs/deploy.md` (only Aman can do this); the demo GIF (needs a recording).
6. Teaching pass over `docs/learn/phase-2..6.md` once Aman says the system is good enough.
5. Known gaps: turns must be ingested in time order; two simultaneous turns of one user could
   both ADD near-identical facts; one shared API key (no per-tenant keys yet); `recall` does one
   LLM call per query for rewrite (skip it when `context` is empty and no time words if cost
   matters).

## Open questions
- None. LLM: Aman's personal Anthropic key is in `.env` (gitignored, verified working with a
  1-token call), Haiku 5.5 for every role (ADR 0012). Judge for Phase 5 still pinned (ADR 0007).

---

## 2026-10-29: everything left in the plan is built; evaluation not yet run
**Done** (build first, then tested at the end, as Aman asked)
- Phase 3: `rank.py` + `MemoryStore.search(rank=, now=)` rerank, `touch` on recall, migration
  0004 (`importance`, `last_accessed_at`, `archived_at`), `decay.py` nightly job, `unarchive`.
  ADR 0016. `rewrite=False` option on recall/API/SDK (skips the LLM rewrite).
- Phase 4: `client.py` Python SDK (`Memory(user_id).add/search/history/forget`).
- Phase 5: LoCoMo converter (`eval/locomo.py`, ADR 0017), per-history ingest lock and RAG index
  cache, `--dataset`, `--no-rerank`, `eval/agreement.py` + `stats.kappa`, `eval/report.py`
  rewritten (merges ablation runs, plan-targets table, load table, judge agreement).
- Phase 6: `telemetry.py` (spans per node/step, token + cost counters, no content), locust load
  test, `Dockerfile`, compose `migrate` + `api`, `deploy.yml` (OIDC, migrate as a job), CI builds
  the image, `docs/deploy.md`, README rewritten. ADR 0018.
- `docs/learn/phase-5.md`, `phase-6.md`; 3.4/3.5 added to phase-3, 4.5 to phase-4.
- Tests: 36 -> 59 passing (rank, decay, SDK, LoCoMo converter, report, API rewrite=false).

**Learned / surprising**
- Text sent through a shell heredoc loses its backslashes (`
` became a real newline and
  broke `judge.py`); use the Edit tool for anything with backslashes. `ast.parse` over every
  edited file caught it.
- LangGraph's `add_node` type wants the node's parameter *named* `state`; a plain `Callable`
  loses names, so `telemetry.traced` uses a `ParamSpec`.
- `uv run mypy` / `uv run ruff` hit the uv-trampoline bug; `uv run --env-file .env python -m ...`
  works for all of them. `docker compose up -d --build api` applied migration 0004 to the
  existing dev DB without touching 0001-0003's checksums.
- Real telemetry check: one Haiku structured-output call = 525 input / 43 output tokens, and
  `llm.cost` came out as the hand calculation.
- LoCoMo: 1986 questions, 446 adversarial (no gold answer) dropped -> 1540 in 10 conversations;
  4 questions have no evidence sessions (their recall is recorded as null).

**Not done, and why**
- The demo GIF (needs a human recording), cloud deployment (needs Aman's GCP/Neon accounts),
  the real evaluation and load test (cost money; the machine lagged on the first try).

---

## 2026-10-27/28: Phase 5 harness built (uncommitted), smoke-tested on 1 question
**Done**
- `eval/` LongMemEval_s harness: `lme.py` (3 systems: full / rag / ours; seeded stratified
  sampling; parallel extraction then real `ingest_turn` replay), `judge.py` (official prompts),
  `llm_cache.py` (SQLite cache by model+schema+prompt), `stats.py` (paired bootstrap, McNemar,
  Holm; `tests/test_stats.py`), `report.py` (README table from JSON). ADR 0015.
- Ablation switches: `search(retriever=hybrid|vector|text)`, `build_graph(keep_history=False)`
  (+ `store.drop`), `--no-history`. Fix: two facts in one turn replacing the same old fact no
  longer crash apply (test added).
- `eval/results/smoke-1.json`: 1 knowledge-update question, all three correct. Context tokens:
  full 117,658 vs ours 1,922 vs rag 1,651.

**Learned / surprising**
- The smoke run showed rag retrieval_seconds = 44 s, but the query embed measures 4-95 ms in
  isolation, so it was a one-off stall; judge latency from p50/p95 over many questions only.
- `uv run mypy` hits the uv-trampoline bug too; use `uv run --env-file .env python -m mypy`.

---

## 2026-10-27: Phases 3 and 4, the whole thing runs
**Done**
- `read_path.py`: `recall` = LLM query rewrite (+ `as_of` extraction) -> hybrid search -> expand
  to source turns -> `pack` to a token budget (`mode` facts/turns/both). Facts print their `[id]`.
- `store.history` (version chain) and `store.forget` (hard delete + cascade + `deletions` row,
  migration 0003); `get_turns`.
- `service.py` (`MemoryService`, `open_service`), `api.py` (FastAPI, API key, bounded inputs),
  `mcp_server.py` (remember/recall/forget, one fixed user), `serve.py`.
- ADR 0014. `docs/learn/phase-3.md`, `phase-4.md`. README quickstart. `.env.example`.
- Live checks: server on :8000 with `curl` (401 without key; ingest -> UPDATE closed the old
  city; search returned facts + turns); MCP client over stdio (list tools, remember, recall,
  forget, recall again).

**Learned / surprising**
- psycopg sends small Python ints as `smallint[]`; comparing with `bigint[]` needs `::bigint[]`.
- `forget` cascades: two facts from one message are both erased with it (by design, ADR 0014).
- mcp 2.3 `CallToolResult.is_error` (snake_case); `Context.request_context.lifespan_context`.
- `docker` isn't running on a new day; start Docker Desktop before tests.

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
