# 0014. One service object, two thin interfaces; forget by id; fixed user over MCP

**Status:** Accepted, 2026-10-27. Refines the forget path in ADR 0004.

## Context
Phase 4 exposes the system twice: a REST API for apps and an MCP server for agents like
Claude Code. ADR 0004 said `DELETE /memories?subject=...` hard-deletes matches. A
"subject" is a fuzzy phrase, and search always returns *something*, so deleting the top
results of a fuzzy match can erase unrelated memories forever.

## Decision
- `MemoryService` (`service.py`) owns ingest / recall / history / forget. `api.py` (FastAPI)
  and `mcp_server.py` only translate their protocol into those calls.
- **Forget is by id**, never by similarity. An agent calls `recall` (facts print their
  `[id]`), then `forget(id)`. REST: `DELETE /memories?user_id=&ids=...`, and
  `DELETE /users/{id}` for whole-user erasure.
- Forgetting a fact removes its whole version chain, the turns it came from, and every other
  fact derived from those turns (their raw words are gone, so keeping them would keep what the
  user asked to erase). A `deletions` row records counts only (migration 0003).
- **MCP is single-user**: `MEMORY_USER_ID` fixes the user, so tools take no `user_id` and an
  agent cannot read or erase anyone else's memories.
- **REST fails closed**: the app refuses to start without `API_KEY`; every route checks
  `X-API-Key` with a constant-time compare. Inputs have length bounds at the boundary.
- A retried ingest must resend the same `occurred_at` (it is part of the idempotency key,
  ADR 0013); if omitted it defaults to now and a retry is then treated as a new turn.

## Trade-off
Erasure by id needs one extra round trip. Cascading to sibling facts can erase more than the
user pointed at, which is the safe direction for a privacy operation. One shared API key is
not per-tenant auth; real multi-tenant keys are Phase 6.

**In an interview:** "Both interfaces call one service, so there is one place for behavior.
Erasure is by explicit id, because deleting by fuzzy match can destroy the wrong data, and it
cascades to everything derived from the same source, because a leftover derived fact is a
leak."
