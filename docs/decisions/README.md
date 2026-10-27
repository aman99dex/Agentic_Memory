# Architecture Decision Records

One file per decision. Each records the options that were on the table, what was chosen,
why, and what it costs us. When a decision changes, write a new ADR that supersedes the
old one instead of editing history. (This is the same idea the memory system itself uses
for facts.)

Each ADR ends with an **"In an interview"** line: the one-breath answer to "why did you do
it this way?"

| # | Decision | Status |
|---|---|---|
| [0001](0001-postgres-single-store.md) | Postgres + pgvector as the only datastore | Accepted |
| [0002](0002-lexical-search-portable.md) | Native `tsvector` for lexical search, BM25 opt-in | Accepted |
| [0003](0003-facts-as-keys-turns-as-values.md) | Index extracted facts, return the source turns | Accepted |
| [0004](0004-bitemporal-facts-and-hard-forget.md) | Keep history with `valid_to`, plus a real hard delete | Accepted |
| [0005](0005-rrf-fusion.md) | RRF (k=60) first, convex fusion as an experiment | Accepted |
| [0006](0006-embeddings-fastembed.md) | `bge-small-en-v1.5` through fastembed (ONNX) | Accepted |
| [0007](0007-provider-agnostic-llm.md) | Provider-agnostic LLM layer, cheap model for extraction | Accepted |
| [0008](0008-experimentation.md) | Offline paired evaluation first, in-house online A/B | Accepted |
| [0009](0009-deploy-cloud-run-neon.md) | Cloud Run + Neon for the public deployment | Accepted |
| [0010](0010-plain-sql-migrations.md) | Plain SQL migrations, no ORM | Accepted |
| [0011](0011-keyword-query-and-then-or.md) | Keyword query: all words first, any word as a fallback | Accepted |
| [0012](0012-haiku-for-every-role-for-now.md) | Haiku for every LLM role during development | Accepted |
| [0013](0013-labels-not-text-and-guarded-targets.md) | Reconcile LLM returns a label + target, code guards the target | Accepted |
| [0014](0014-one-service-two-interfaces.md) | One service, two thin interfaces; forget by id; fixed user over MCP | Accepted |
