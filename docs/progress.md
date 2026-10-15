# Progress log

Newest entry first. Every session ends by adding one entry here.

## Current status
- **Phase:** 0 done. **Next: Phase 1, storage and plain retrieval.**
- **Branch:** `main` (Phase 0 merged via PR #1, CI green)

## Next session: Phase 1 checklist
1. New branch `phase-1-storage`.
2. `migrations/0001_init.sql` and the migration runner (ADR 0010). Tables:
   - `turns` (raw rounds = the values)
   - `memories` (facts = the keys and versioned state, with `source_turn_ids`,
     `valid_from`/`valid_to`/`superseded_by`, `embedding_model`), per ADRs 0003 and 0004
   - HNSW on the embeddings, GIN on the `tsvector` columns.
3. A `MemoryStore` built on a psycopg async pool: `add`, `search_vector`,
   `search_text`, `get`, `invalidate`.
4. Hybrid search with RRF, k=60 (ADR 0005). Aman writes the RRF function himself; it's
   about 15 lines.
5. **Test the filtered-HNSW trap:** 100 users × 50 memories. Show that a `LIMIT 10`
   without iterative scan comes up short and that iterative scan fixes it (ADR 0001).
6. Add `fastembed` and wrap it in an `Embedder` (ADR 0006).
7. Write `docs/learn/phase-1.md`: RRF, why hybrid search wins on names and IDs, and the
   iterative-scan result.

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
