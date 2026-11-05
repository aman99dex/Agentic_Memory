# Progress log

Newest entry first. Every session ends by adding one entry here.

## Current status
- **Phases 0-7 are on `main`** (Phase 7 pushed 2026-11-06 as 0ec2cda, plus two fixes found by
  testing the pushed code: f4b2256, e109914). CI is green on all three. Why Phase 7:
  `docs/intent/usable-product.md`. ADRs 0019-0023, `docs/learn/phase-7.md`.
- **No hosted production exists**: the deploy workflow skips itself until `GCP_PROJECT_ID`
  is set (docs/deploy.md, Aman's accounts). "Production" was tested as the Docker stack.
- What Phase 7 gives: local mode on SQLite (no Docker), saving that returns in milliseconds with
  a background worker under per-user leases, user vs project scopes, session summaries and
  profiles, a session-start briefing, a Claude Code plugin (three hooks + MCP), tenant API keys,
  a client-only `pip install`, bulk import, an `agentic-memory` CLI.
- Verified 2026-11-04: 110 tests (store tests on both SQLite and Postgres; real Haiku for the
  LLM ones), ruff + strict mypy clean, Docker image builds and runs; a fresh `uv tool install`
  plus two real `claude -p` sessions in a scratch repo (the second answered from memory, a
  third folder got only the user-wide facts); `setup claude-code` installed the plugin into a
  throwaway Claude config; `examples/chat.py` against a live Postgres server remembered
  "vegetarian, Pune" across two runs.
- **What has NOT been run:** the real evaluation (only a 1-question smoke test exists), the
  ablations, LoCoMo through the LLMs, the locust load test, the CD workflow, the 50 hand labels.
  `setup claude-code` against the real GitHub repo (needs the plugin pushed to main), and the
  plugin in Aman's own Claude Code (his call: it would capture every session he runs).
- **Workflow (Aman, 2026-10-26): build everything end to end first, teach last.**
  `docs/learn/phase-2..7.md` are written and waiting to be taught.

## Next session: start here
1. Phase 7 is pushed and verified from GitHub (see the 2026-11-06 entry). Orbis's bridge
   works with it; three Orbis-side notes were sent to the Orbis session, not changed here.
2. **Run the evaluation, on an idle machine** (the first attempt made Aman's machine lag).
   Phase 7 changed the extraction schema (a `scope` per fact) and the recall rewrite
   (`past_sessions`), so every cached extraction is stale: the first run re-extracts.
   The harness replays turns with `ingest_turn` and never runs the worker, so it measures
   facts + turns without session summaries or profiles; an `--episodes` ablation that runs
   `Worker.summarize_session` per haystack session is the obvious next comparison.
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

## 2026-11-06: pushed, then tested as a user would (local, production stack, Orbis)
**Done**
- Pushed Phase 7 to main. CI green (it now also starts the image as its real user).
- **Local, from GitHub:** `uv tool install ...@git+https://github.com/aman99dex/Agentic_Memory`
  (15 s) and `agentic-memory setup claude-code` (fetches the plugin from GitHub) in a throwaway
  Claude config; two real `claude -p` sessions in a scratch repo: the second answered the
  project, where it ships, last session's work and the commit style from memory alone.
- **Production stack:** fresh clone, `docker compose up` on spare ports: migrations 0001-0005,
  401 without a key, tenant key from `docker compose exec api agentic-memory keys create`,
  `examples/chat.py` remembered "vegetarian, Pune", save 26 ms, import + re-import no-op, the
  operator key couldn't see the tenant's user, the briefing caught up with a new allergy.
- **Orbis** (its commit 485cab8, CLI bridge): `orbis_recall` in local mode returned the right
  scope (project + user-wide inside the repo, user-wide only elsewhere); server mode worked
  against the stack; a session with the plugin + Orbis MCP saved a note via `orbis_write`, and
  a fresh session answered from both the memory briefing and `orbis_search`.

**Bugs found by testing the pushed code, fixed**
- The API container crashed on start: every command made `~/.agentic-memory` for its log, and
  the image's user has no home. Logging falls back to stderr; a test and a CI step cover it.
  The earlier image check had set `AGENTIC_MEMORY_HOME` itself, which hid this.
- CLI output was CRLF on Windows, doubling every line inside Orbis. Now plain `\n`.
- Added `recall --no-rewrite` (about 2 s faster per call, but no session summaries).

**Learned**
- Claude Code clones a marketplace's whole repo into its config folder; a very deep config path
  hits Windows' 260-character limit. A normal `~/.claude` path stays at 140.
- `claude -p` with the real config writes Claude Code's own memory notes for the scratch repo;
  clear them before a "does it remember" test, or the answer may not come from us.

---

## 2026-11-04: Phase 7, usable by real people (pushed 2026-11-06)
**Why.** Aman asked for a brutal comparison with TencentDB Agent Memory, Mastra Observational
Memory and Mem0, then for the system to be usable before any evaluation. A dogfood run of a
developer's week showed: five setup steps, nothing saved unless the agent calls `remember`,
1.4-6.2 s per save, no project scope, no memory of what happened, one shared key.

**Done**
- SQLite local mode (`sqlite_store.py`, ADR 0019); `store.py` split into types/interface,
  `postgres_store.py`, `db.py`; migrations moved into the package; Postgres migration 0005.
- Background saving: queue in the turns table, `ingest_leases`, `worker.py` (ADR 0020).
- Scopes, episodes, profiles, `context.py` (ADR 0021); `past_sessions` in the recall rewrite.
- `cli.py` (`agentic-memory`): setup, status, context, recall, import, worker, serve, mcp,
  migrate, keys, hook. `plugin/` + `.claude-plugin/marketplace.json` (ADR 0022).
- Tenant keys, `[server]` extra, `/memories:import`, `/memories:context`, SDK `context()` and
  `import_turns()`, `examples/chat.py` (ADR 0023).
- Tests: 59 -> 110. New: `test_queue_and_scopes.py`, `test_worker.py` (scripted model),
  `test_local_mode.py` (hooks through the CLI, one real end-to-end run).

**Measured** (same developer week, before -> after): save 1.4-6.2 s -> 1-6 ms; "moving off
Black" 2 facts -> 1; session start had nothing -> profile, project brief, last 3 sessions;
"what did we do on the first day?" nothing -> the Oct 1 session first. Session-start hook
0.6 s warm, prompt hook 0.2-0.4 s. Client-only install: 8 packages.

**Learned / surprising**
- A summary searched alongside facts ranked "said they live in Bangalore" above the current
  "lives in Pune". Summaries are history: searched only for past-work questions (ADR 0021).
- Claude Code kills hook processes when `claude -p` exits, even `async: true` ones: the first
  plugin stored nothing. The Stop hook now stores (no LLM) and starts a detached worker.
- `logging.basicConfig` does nothing once any handler exists (pytest adds one): use a named
  logger with its own FileHandler.
- PowerShell pipes text with a UTF-8 BOM; read hook stdin as `utf-8-sig`.
- A lazy embedder broke the Docker warm-up step (it no longer downloaded anything, so
  `/models` didn't exist): the warm-up now embeds a word.
- Haiku 5.5 replies can start with a thinking block: take the text blocks, not `content[0]`.
- With 10 results shared between facts and summaries, facts crowded out the summaries:
  summaries are now searched on their own for past-work questions.

**Not done, and why**
- The evaluation (next, per the intent doc), pushing (Aman's call), installing the plugin into
  Aman's own Claude Code (it would capture all his sessions; his call), the demo GIF.
- SQLite has no nightly decay job (archiving after a cold year); Postgres only for now.
- Importing Claude Code's past transcripts: their format is undocumented and changes.

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
