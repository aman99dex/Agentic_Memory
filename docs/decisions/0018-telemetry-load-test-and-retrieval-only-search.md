# 0018. OpenTelemetry without content, a load test that separates the LLM from retrieval

**Status:** Accepted, 2026-10-29. Phase 6.

## Context
Phase 6 asks for spans per graph node, token and cost counters, and a locust load test with p95 at
50 concurrent users. Two facts shape it. First, `recall` makes an LLM call (the query rewrite), so
the plan's "p95 recall under 150 ms" cannot hold for that path: a Haiku round trip alone is
hundreds of milliseconds. Second, the memory text is personal data (ADR 0004).

## Decision
- **OpenTelemetry API everywhere, SDK only when configured** (`telemetry.py`). With no
  `OTEL_EXPORTER_OTLP_ENDPOINT` the calls are no-ops; with it, spans and metrics go out over
  OTLP/HTTP. Any backend (Jaeger, Grafana, Honeycomb) works; none is bundled.
- **Spans:** `ingest` (whole write), `extract` / `reconcile` / `apply` (each graph node),
  `recall` with `recall.rewrite` / `recall.search` / `recall.expand_and_pack`.
- **Counters:** `llm.tokens` by role, model and direction, read from each response's
  `usage_metadata` through a LangChain callback attached to the model. `llm.cost` (USD) is only
  recorded when the model prices are given in env vars: prices change, and a wrong hard-coded
  price would be a fabricated number.
- **No message content in any span or metric attribute,** only ids, counts and sizes.
- **`rewrite=false` on search** (REST field and SDK argument) skips the LLM query rewrite. The
  load test mixes `search (no rewrite)` (retrieval alone: embed plus Postgres) with
  `search (LLM rewrite)` and `ingest`, each reported separately, so the retrieval number is honest
  and the LLM-bound numbers are not hidden.
- **Load test** (`loadtest/locustfile.py`, locust in a separate dependency group): every simulated
  user is its own tenant, seeded then erased. With `LOAD_NAME` it writes
  `eval/results/<name>.json`, which `eval.report` renders, so no latency is typed by hand.

## Trade-off
- Skipping the rewrite loses pronoun resolution and the `as_of` time filter; it is opt-in per
  request. The default stays on because it is the better-quality path.
- Ingest and rewritten searches in the load test spend real LLM money and hit provider rate
  limits, so p95 there measures the provider as much as this service. The report labels
  endpoints so that is visible.
- Cost counters are only as right as the prices you set.

**In an interview:** "I separated what my code controls from what the LLM provider controls:
retrieval p95 is measured on its own, the LLM-bound paths are reported next to it, and telemetry
carries ids and counts but never the text of a memory."
