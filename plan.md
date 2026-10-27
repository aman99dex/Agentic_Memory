# Plan — Agentic Memory: a long-term memory layer for LLM agents

> **Revised 2026-10-16 after research.** The changes are below. The original plan
> follows, unchanged except where these revisions override it. Each change links to its
> ADR in `docs/decisions/`.
>
> 1. **Retrieval unit (ADR 0003):** store raw turns as well as extracted facts. Facts
>    are search keys and versioned state; retrieval returns the source turns
>    (LongMemEval's fact-augmented key expansion, +9% recall).
> 2. **Read path:** add *time-aware query expansion*: the LLM extracts a time range and
>    the search filters on it. This is the largest measured gain on temporal questions.
> 3. **Lexical search (ADR 0002):** built-in `tsvector` + `ts_rank_cd` behind one
>    function. BM25 (`pg_textsearch`) becomes an ablation, because Neon retired
>    pg_search.
> 4. **Filtered HNSW (ADR 0001):** `hnsw.iterative_scan` on every tenant query, plus a
>    test for it.
> 5. **LLM (ADR 0007):** provider-agnostic `init_chat_model`; extraction cached by a
>    hash of the session content. The judge is pinned to LongMemEval's official
>    GPT-4o judge.
> 6. **Migrations (ADR 0010):** plain SQL files instead of Alembic.
> 7. **Embeddings (ADR 0006):** fastembed (ONNX), no PyTorch in the image.
> 8. **A/B (ADR 0008):** offline paired bootstrap CIs + McNemar + Holm, with ablations
>    on a stratified 150-question dev sample. Online: hash assignment + team-draft
>    interleaving, explicitly underpowered.
> 9. **Deploy (ADR 0009):** Cloud Run + Neon PG18; GitHub Actions through Workload
>    Identity Federation.
> 10. **Privacy (ADR 0004):** a hard-delete `forget` alongside the versioned history.
>     A memory-poisoning test is added to the eval (OWASP LLM01/LLM04).
> 11. **Stack versions (2026-10):** Postgres 18, pgvector ≥ 0.8.2 (CVE-2026-3172),
>     LangGraph 1.2, `mcp` 2.x (`FastMCP` is now `MCPServer`), Streamable HTTP + stdio.
>
> **Budget:** about $20 for extraction on Haiku 5.5. Answering and judging the full 500
> questions is about $15–30 per config. Every ablation runs on the 150-question dev
> sample first.

## Where this repo is today (be honest with yourself)

The current repo is the DeepLearning.AI course on agent memory: 4 notebooks, plus a
2,000-line `helper.py` written for Oracle DB, and the course's `requirements.txt`. The
README says "built by the author". An interviewer who has taken the course will
recognise it immediately. **Step 0 fixes this.** The notebooks move to
`archive/course-notebooks/` with credit to the course, and the real project is built
fresh next to them.

## What we're building

A self-hostable **memory service for agents**. It plugs into Claude Code, Cursor or any
LangGraph agent through **MCP**, and it ships with a **public benchmark that has real
numbers**. The pitch on the resume:

> "Agents forget. This gives them memory that updates itself, knows when a fact stopped
> being true, and fits under a token budget. Here's the LongMemEval score versus full
> context and versus plain RAG."

The three things AI engineers check, all of which this delivers:
1. **It's evaluated:** accuracy, recall, latency and tokens on a public benchmark.
2. **It handles changing facts:** "I moved to Pune" replaces "I live in Bangalore"; the
   old fact isn't kept and retrieved alongside the new one.
3. **It's usable:** one `docker compose up`, an MCP server, and a Python SDK.

## Stack

| Concern | Choice | Why |
|---|---|---|
| Language | Python 3.12, `uv` for dependencies | You already work in Python + FastAPI at Neoflo |
| API | FastAPI + Pydantic v2 | Same as Collections |
| Agent / memory pipeline | **LangGraph** | What AI teams actually use; explicit state machine |
| Storage | **Postgres 16 + pgvector** (HNSW index) + `tsvector` | One database for vectors, full-text search and relational data. You already have pgvector experience (`pgvecsync`, `RAG-pgvectorscale`) |
| LLM | Claude via `langchain-anthropic` (Sonnet for dev and eval, Haiku for extraction) | Keeps cost down; the model is a config value |
| Embeddings | `bge-small-en-v1.5` locally (sentence-transformers); swappable | Free, fast, runs offline |
| MCP | official `mcp` Python SDK (FastMCP) | Usable from Claude Code / Cursor |
| Observability | OpenTelemetry traces + LangSmith (optional) | Same idea as `neoflo-logger` / `neoflo-metrics` |
| Tests / CI | pytest, testcontainers-postgres, ruff, mypy, GitHub Actions | |

## Architecture

```
          ┌──────────── clients ────────────┐
   Claude Code / Cursor (MCP)     Python SDK     REST
          └──────────────┬──────────────────┘
                         ▼
                FastAPI  /  MCP server
                         │
     ┌───────────── LangGraph write path ─────────────┐
     │ extract → embed → find similar → decide         │
     │ (ADD | UPDATE | INVALIDATE | NOOP) → persist    │
     └─────────────────────────────────────────────────┘
     ┌───────────── read path ─────────────────────────┐
     │ query rewrite → hybrid search (vector + BM25,    │
     │ RRF fusion) → recency/importance rerank →        │
     │ pack under token budget                          │
     └─────────────────────────────────────────────────┘
                         │
              Postgres + pgvector
   memories(id, user_id, namespace, type, content, embedding,
            importance, valid_from, valid_to, superseded_by,
            source_msg_ids, created_at, last_accessed_at, access_count)
```

**Memory types** (keep this to 3; the course's 7 is too many):
- `episodic`: what happened in a session (summaries)
- `semantic`: facts and preferences about the user or world ("prefers dark mode")
- `procedural`: learned how-tos and successful tool sequences

**Changing facts over time** (the differentiator): never hard-delete a memory. When a
fact is contradicted, set `valid_to` on the old row and point `superseded_by` at the new
one. Queries see current facts by default, and `as_of=<date>` answers "what did I
believe in March?". This handles LongMemEval's *knowledge-update* and *temporal* question
categories.

## Phases

Each phase ends with something working and committed. Rough timing assumes evenings and
weekends.

### Phase 0: Clean slate (½ day)
- [x] Move the notebooks and `helper.py` to `archive/course-notebooks/`. Rewrite the
      README to credit the course honestly.
- [x] Set up the `uv` project, ruff, mypy, pre-commit, and a GitHub Actions CI skeleton.
- [x] `docker-compose.yml` with `pgvector/pgvector:0.8.7-pg18` (pre-commit skipped: CI runs the same checks).
- **Learn:** how pgvector's HNSW and IVFFlat indexes differ; the Postgres `tsvector`
  basics.

### Phase 1: Storage and plain retrieval (week 1)
- [x] Schema and migration (plain SQL + runner, ADR 0010); HNSW index on `embedding`; GIN index on `tsvector`.
- [x] `MemoryStore` with `add`, `search_vector`, `search_text`, `get`, `invalidate` (+ `supersede`, hybrid `search`, `as_of`).
- [x] **Hybrid search with Reciprocal Rank Fusion** (k=60). Write it yourself; it's
      about 15 lines.
- [x] Tests run against a real Postgres via testcontainers.
- **Learn:** RRF; why hybrid search beats vector-only on names, IDs and numbers.

### Phase 2: The write path in LangGraph (week 2)
- [x] `extract` node: an LLM turns a conversation turn into candidate facts, as
      structured output (Pydantic).
- [x] `reconcile` node: for each candidate, fetch the top-k similar memories. The LLM
      decides `ADD | UPDATE | INVALIDATE | NOOP`, and the edge is followed
      deterministically.
- [x] Changes to existing facts keep history: set `valid_to` and `superseded_by` on the
      old row.
- [x] Idempotency: re-ingesting the same message doesn't duplicate memories (store a
      hash of the source message).
- **Learn:** LangGraph state, conditional edges and checkpointers; the Mem0 paper's
      update operations, and where this design differs from it.

### Phase 3: The read path and context packing (week 3)
- [x] Query rewrite for questions that refer back ("what did she say about it?").
- [ ] Rerank: `score = rrf * (1 + importance) * recency_decay(last_accessed_at)`.
      Expose the weights in config.
- [x] A **token-budget packer**: greedily fill N tokens, deduplicate, keep current
      facts over superseded ones. (Done: facts first, then source turns, deduped by id.)
- [ ] Forgetting: a nightly job decays importance and archives memories that are cold
      and low-importance. Archived memories are not deleted.
- **Learn:** the "lost in the middle" problem and why the order of packed context
      matters.

### Phase 4: Interfaces (week 4)
- [x] REST API: `POST /memories:ingest`, `POST /memories:search`,
      `GET /memories/{id}/history`. Multi-tenant through `user_id` and `namespace`.
- [x] **MCP server** exposing three tools: `remember`, `recall` and `forget`.
- [ ] Demo: add it to Claude Code (`claude mcp add`) and record a 60-second GIF of it
      remembering across sessions.
- [ ] A small Python SDK: `Memory(user_id).add(...)` and `.search(...)`.
- [x] API-key auth on the REST API. Validate input at the boundary.

### Phase 5: Evaluation, the part that sells it (weeks 5–6)
- [ ] An `eval/` harness for **LongMemEval** (the 500-question `_s` split) and
      **LoCoMo**.
- [ ] Three systems compared on the same answering model:
  1. **Full context**: the whole history stuffed into the prompt (upper bound on cost)
  2. **Naive RAG**: chunk the history, vector top-k
  3. **This system**
- [ ] Metrics:
  - QA accuracy, judged by an LLM using the benchmark's own judge prompt
  - Recall@k of the evidence sessions
  - p50 and p95 latency for reads and writes
  - Tokens per answer
- [ ] Break the results down by category: single-session, multi-session, knowledge
      update, temporal reasoning, abstention.
- [ ] An ablation table: switch off hybrid search, reranking, and history-keeping one at
      a time. Show what each piece is worth.
- [ ] Save the results as JSON in `eval/results/`, and generate the README table from
      them. **Never hand-type a number.**
- **Learn:** LLM-as-judge pitfalls; why ablations matter more than one headline number.

### Phase 6: Production polish (week 7)
- [ ] OpenTelemetry spans for each graph node, plus token and cost counters.
- [ ] Load test with `locust`; record p95 at 50 concurrent users.
- [ ] Docker image, `docker compose up` brings up everything, `.env.example`.
- [ ] README: a 30-second pitch, the architecture diagram, the results table, a quick
      start, and design decisions with their trade-offs.
- [ ] Tag `v0.1.0`.

## Definition of done (what makes it "production level")
- One command brings up the whole stack, and CI is green: lint, type-check, unit tests
  and integration tests.
- Every number in the README is reproducible with `make eval`.
- Changing facts, idempotent ingestion, tenant isolation and API auth all have tests.
- Traces show the time spent in every node.

## Numbers to fill into the resume (the targets I wrote there)

The resume currently says the following. Replace each with the **measured** value once
Phase 5 and Phase 6 are done. If you miss a target, put the real number in; an honest
number still sells.

| Resume claim | Target | Measured |
|---|---|---|
| LongMemEval accuracy vs naive RAG | +15 points | |
| Tokens per answer vs full context | −90% | |
| p95 recall latency | < 150 ms | |
| Knowledge-update category accuracy | > 80% | |

## Reading list
- LongMemEval (Wu et al., 2024) and LoCoMo (Maharana et al., 2024): the benchmarks
- Mem0 paper (2025): the update operations
- MemGPT / Letta: the idea of tiered memory
- Zep / Graphiti: temporal knowledge graphs (bi-temporal edges)
- The pgvector README, especially the sections on HNSW tuning and filtered search
