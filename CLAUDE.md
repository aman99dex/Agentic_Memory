# Agentic Memory: context for Claude sessions

A self-hostable long-term memory service for LLM agents. This is Aman's **resume
project**: everything must be defensible in an interview, and Aman is **learning** while
it gets built.

## Start every session by reading
1. `docs/progress.md`: where the last session stopped and what comes next. **Source of truth for status.**
2. `plan.md`: the roadmap. The "Revised" block at the top overrides the original text below it.
3. `docs/decisions/README.md`: the ADR index. Read the ADRs for whatever this session touches.

## End every session by
- Updating `docs/progress.md`: what got done, what's next, open questions, and anything surprising.
- Ticking the boxes in `plan.md` for finished items.

## How we work (non-negotiable)
- **Every non-trivial decision gets an ADR** in `docs/decisions/NNNN-*.md`: context, the
  options considered, the decision, why, the trade-off, and an **"In an interview"**
  one-liner. Add it to the index table. Never edit an accepted ADR's decision;
  supersede it with a new one.
- **Every phase gets a learning note** in `docs/learn/phase-N.md`: the concepts behind
  the code, explained plainly, plus a "Try it yourself" section and reading. Write it
  for Aman, not for a reviewer.
- **Explain while building.** When introducing a technique (RRF, HNSW, LangGraph edges,
  McNemar…), say what it is and why it's used here, briefly, in chat or in the learning
  note.
- **Never hand-type an eval number.** Results live as JSON in `eval/results/`, and the
  README tables are generated from them. Comparisons follow ADR 0008: paired, bootstrap
  CI, McNemar, Holm.
- **Verify versions and APIs against current docs** before using them. Libraries here
  move fast. In the `mcp` SDK 2.x, `FastMCP` was renamed `MCPServer`.
- Work on a branch per phase (`phase-N-<slug>`). Commit only when Aman asks.
- **Do not use the Neoflo or Collections API keys here.** LLM provider and models come
  from env vars (ADR 0007).

## Stack (see the ADRs for why)
Python 3.12–3.13 + uv · Postgres 18 + pgvector 0.8.7 (HNSW, `hnsw.iterative_scan` on
tenant queries) · built-in `tsvector` + `ts_rank_cd` · RRF k=60 · fastembed
bge-small-en-v1.5 · LangGraph 1.x · `init_chat_model` · FastAPI · `mcp` 2.x · psycopg 3
async, plain SQL migrations · Cloud Run + Neon.

## Commands
```bash
uv sync                      # install
docker compose up -d db      # local Postgres (Docker Desktop must be running)
uv run pytest                # tests (testcontainers starts its own Postgres)
uv run ruff check . && uv run ruff format --check . && uv run mypy   # what CI runs
```
On Windows, Docker Desktop lives at
`%LOCALAPPDATA%\Programs\DockerDesktop\Docker Desktop.exe`.

## Layout
- `src/agentic_memory/`: the package
- `tests/`: pytest; integration tests run against real Postgres, never mocks
- `migrations/`: `NNNN_name.sql`, forward-only (ADR 0010)
- `eval/`: benchmark harness; `eval/results/*.json` is committed and `eval/cache/` is ignored
- `docs/decisions/`: ADRs · `docs/learn/`: learning notes · `docs/progress.md`: session log
- `archive/course-notebooks/`: the DeepLearning.AI course material. **Not part of the
  project.** Excluded from lint; don't import from it.
