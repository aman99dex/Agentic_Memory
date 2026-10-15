# 0002. Native `tsvector` for lexical search, BM25 opt-in

**Status:** Accepted, 2026-10-16

## Context
Vector search misses exact tokens such as names, IDs, numbers and rare words
("PR #4821", "Dr. Okonkwo"). We want a keyword ranker next to it. True BM25 in Postgres
needs an extension, and the managed hosts have moved around:
- **Neon retired ParadeDB `pg_search` on 2026-09-21.**
- Tiger's `pg_textsearch` (PostgreSQL license) runs on Tiger Cloud, AlloyDB and Cloud SQL,
  but not on Neon or RDS.
- Built-in `ts_rank_cd` runs everywhere, but it is *not* BM25: it has no IDF and no
  document-length saturation.

## Options
1. `pg_search`: AGPL, and not available on our target host.
2. `pg_textsearch`: real BM25, but not available on Neon.
3. **Built-in `tsvector` + GIN index + `ts_rank_cd`**, behind one SQL function so the
   ranker can be swapped.

## Decision
Option 3. Self-hosters can opt into `pg_textsearch` through the same function signature.

## Why "not BM25" is acceptable here
Reciprocal Rank Fusion (ADR 0005) uses **only the rank order** from each retriever, not
the scores. What matters is whether the right document lands in the lexical top 20, and
for short fact strings `ts_rank_cd` gets that mostly right. This is a **testable claim**:
Phase 5 runs `ts_rank_cd` vs `pg_textsearch` as an ablation.

**In an interview:** "Managed BM25 support is in flux. Neon just dropped pg_search. RRF
only looks at ranks, so I used the portable ranker and made BM25 a measured ablation
rather than a dependency."
