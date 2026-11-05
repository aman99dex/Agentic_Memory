# 0023. Products: tenant keys, a client-only install, bulk import

**Status:** Accepted, 2026-11-04. Phase 7. Builds on ADR 0014 (one service, REST + MCP).

## Context
A product team adopting this hit three walls: one shared API key for everyone, a Python SDK
that installed LangChain, ONNX and Postgres drivers just to make HTTP calls, and no way to load
existing chat history.

## Decision
- **Tenant keys.** `agentic-memory keys create <tenant>` prints a random key once and stores
  only its SHA-256. A request's key resolves to a tenant, and the API stores that tenant's user
  ids as `<tenant>:<user_id>`, through one `uid()` helper that every route uses. Responses show
  the caller's own user id. `API_KEY` from the environment stays as the operator's key, with
  unprefixed ids, so existing data and deployments are unchanged. Tenant names are limited to
  lowercase letters, digits and dashes, so a prefix can't be forged with a colon.
- **Client-only install.** `pip install agentic-memory` installs the SDK and httpx only (8
  packages). `agentic-memory[server]` adds everything else. The dev dependency group depends on
  `agentic-memory[server]`, so `uv sync` still gives contributors the whole thing.
- **Bulk import.** `POST /memories:import` takes up to 1000 turns with required timestamps and
  queues them oldest first; the SDK's `import_turns` chunks larger lists. Re-importing the same
  turns is a no-op (the turn hash, ADR 0013).
- **Namespaces and context over REST.** Every call takes an optional `namespace` (ADR 0021), and
  `POST /memories:context` returns the briefing for a system prompt.
- `examples/chat.py` is the whole integration in about 20 lines, and was run against a live
  server: the second message got vegetarian places in Pune without being told again.

## Trade-off
- Tenant isolation is by id prefix, not separate tables or row-level security. One helper is
  the only path from a request to a user id, and a test checks that another tenant, and the
  operator, can't see a tenant's user.
- The operator key can address any id, including a prefixed one. It is the admin key.
- No per-tenant rate limits or quotas; put the API behind a gateway for that.

**In an interview:** "A product installs an 8-package client and gets a scoped API key; the
server keeps tenants apart through a single id-mapping function that a test attacks from both
sides."
