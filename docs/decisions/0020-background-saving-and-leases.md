# 0020. Saving returns at once; a worker processes turns under a per-user lease

**Status:** Accepted, 2026-11-04. Phase 7. Changes the default of ADR 0013's `ingest_turn`
flow (it is still used by the evaluation and by `wait=True`).

## Context
Saving one turn ran extraction, one reconcile call per fact and the writes before it returned:
1.4 to 6.2 seconds per turn in the 2026-11-04 session. A chat product can't wait that long per
message, and an agent hook can't either. Two known gaps came from the same design: two turns of
one user saved at once could both ADD the same fact, and turns had to arrive in time order.

## Options
1. **Keep it synchronous.** Simple, and too slow for products.
2. **An in-process queue** (`asyncio.Queue`). Fast, but a crash loses queued turns, and two API
   replicas still race on one user.
3. **The database is the queue.** The turn is stored with `processed_at = NULL`; any worker
   claims a user with a lease row, processes that user's turns oldest first, and marks each one
   done.
4. **A broker** (Redis, SQS, Kafka). Another service to run, for a problem the database solves.

## Decision
- **Option 3.** `ingest` stores the turn (no LLM, no embedding: 1 to 6 ms locally) and returns
  `{turn_id, queued}`. `wait=True` keeps the old behaviour for callers that want the steps.
- **Leases:** `ingest_leases(user_id, holder, expires_at)`. A claim is one upsert that wins
  only if the lease is free, expired, or already the caller's. It is renewed before every turn
  and released at the end; a crashed worker's lease frees itself after 5 minutes.
- **Order:** pending turns are read `ORDER BY occurred_at, id`, so facts are applied in the
  order they became true. A turn that arrives late and is older than the fact it replaces closes
  that fact at its own start (`greatest(at, valid_from)`), instead of breaking the
  `valid_to >= valid_from` check.
- **Failures:** a failing turn gets `attempts + 1` and `last_error`, and the worker moves on to
  the next turn. After 5 attempts it stops being retried; `agentic-memory status` shows it.
- **Where workers run:** a background task in the API and in the MCP server, a detached process
  started by the Claude Code Stop hook (ADR 0022), or `agentic-memory worker`. Leases make any
  number of them safe.
- Turn vectors are no longer computed: nothing ever searched them (ADR 0003 searches facts).

## Trade-off
- A fact appears seconds after the save, not at once. Callers that need it immediately pass
  `wait=True`.
- The Postgres queue is polled every 2 seconds when idle (and woken at once by a save in the
  same process). `LISTEN/NOTIFY` would remove the poll; not needed at this scale.
- One user's turns are processed one at a time. Different users run in parallel across workers.

**In an interview:** "The database is the queue: a save is an insert, and a per-user lease
serialises the LLM work, which fixed both the latency and a duplicate-fact race in one move,
with no broker to run."
