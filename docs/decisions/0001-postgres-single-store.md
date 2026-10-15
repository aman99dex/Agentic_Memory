# 0001. Postgres + pgvector as the only datastore

**Status:** Accepted, 2026-10-16

## Context
Memory needs four things: vector similarity search, keyword search, relational data
(tenants, history chains, experiment logs) and transactions. A "fact superseded by
another fact" update must change two rows atomically.

## Options
| Option | For | Against |
|---|---|---|
| **Postgres + pgvector** | One system holds vectors, full-text and relational data. ACID updates across the old and new fact. Available on every managed host. | Vector search is slower than dedicated engines at around 100M vectors. We'll never get near that. |
| Dedicated vector DB (Qdrant, Weaviate, Pinecone) | Faster ANN at scale, built-in hybrid search | A second system to run. The relational data still needs Postgres, and the two can drift apart with no transactions spanning them. |
| Graph DB (Neo4j, as Zep/Graphiti and Mem0g do) | Multi-hop entity queries | Heavy to operate. Graphiti's gains come mostly from *bi-temporal edges* and *hybrid retrieval*, and both work in plain tables (ADR 0004, 0005). |

## Decision
Postgres 18 + pgvector 0.8.x, with an HNSW index (`m=16, ef_construction=64`). Every
tenant-filtered query sets `SET LOCAL hnsw.iterative_scan = relaxed_order`.

## Why the iterative-scan setting matters
An HNSW scan picks the top `ef_search` (default 40) neighbours first and applies
`WHERE user_id = $1` *afterwards*. With 1,000 users, a query can return 0 of your 10 rows
because the 40 nearest neighbours belonged to other users. Since 0.8.0, iterative scan
keeps walking the graph until enough rows pass the filter, up to `hnsw.max_scan_tuples`.
**This is a real multi-tenant bug that most pgvector tutorials have.** Phase 1 includes a
test for it.

## Consequences
- Use pgvector ≥ 0.8.2, because of CVE-2026-3172 (an overflow in parallel HNSW builds).
- pgvectorscale (DiskANN) only pays off at millions of vectors per node, so we skip it.

**In an interview:** "Memory updates have to be transactional: invalidate the old fact
and insert the new one together. One Postgres gives me that plus vectors and full-text.
The trap is filtered HNSW search returning too few rows, and I handle it with iterative
scans and a test."
