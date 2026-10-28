# Phase 4: Interfaces (REST API and MCP)

The system works in Python. Phase 4 lets other programs and agents use it.

```
  apps ──HTTP──► api.py ─┐
                         ├─► MemoryService ─► write path / read path / store
  agents ─MCP──► mcp_server.py ┘
```

Code: `service.py`, `api.py`, `mcp_server.py`, `serve.py`. Tests: `tests/test_api.py`.

**Teaching status:** written while building, before any teaching. Teach one section at a time.

---

## 4.1 One service, two thin interfaces

**Picture it.** A restaurant kitchen with two windows: a drive-through and a counter. Both pass
orders to the same kitchen. Nobody cooks in the windows.

**Plainly.** `MemoryService` is the kitchen: ingest, recall, history, forget. The REST API and
the MCP server only translate their protocol into those four calls. A rule fixed in one place is
fixed for both.

**Interview.** "One service, thin adapters: protocol code and behavior never mix."

<details><summary>Check: where would a new "export memories" feature go first?</summary>
In `MemoryService`; then each interface gets a few lines.
</details>

---

## 4.2 REST: the boundary is where you check everything

**Plainly.** Anything arriving over HTTP is untrusted. `api.py` checks it before it reaches the
database or the LLM:
- **Auth:** header `X-API-Key`, compared with `hmac.compare_digest` (takes the same time whether
  the first or last character is wrong, so timing can't leak the key). The app **refuses to
  start** without `API_KEY`: failing closed beats running open by accident.
- **Validation:** Pydantic models bound every length (a 1 GB message would otherwise reach the
  LLM and our wallet). Bad input gets `422` before any work happens.
- **Idempotent retries:** send the same `occurred_at` and the same turn is stored once (ADR 0013).

Routes: `POST /memories:ingest`, `POST /memories:search`, `GET /memories/{id}/history`,
`DELETE /memories?user_id=&ids=`, `DELETE /users/{id}`.

**Deep.** The `:ingest` / `:search` suffix is the Google API "custom method" style: a verb on a
collection that isn't plain CRUD. In tests we call the real ASGI app through `httpx`'s
`ASGITransport`, so auth, validation and routing are all exercised without opening a port.

<details><summary>Check: why 401 before 422?</summary>
Auth is a dependency on the whole app, so it runs first; an unauthenticated caller shouldn't
learn anything about the request format.
</details>

---

## 4.3 Forgetting is deliberate (ADR 0014)

**Plainly.** Changing a fact keeps history (Phase 1). *Erasing* is different: it is permanent.
We erase by explicit `id`, never "whatever matches this phrase", because search always returns
something and a fuzzy delete can destroy the wrong data. Erasing a fact also removes: its
older and newer versions, the turns it came from, and every other fact from those turns. We keep
a `deletions` row with counts only.

**Seen for real.** In the MCP smoke test, "I prefer dark mode and my favourite language is
Rust" produced two facts from one message. Forgetting one erased both and the message. That is
the privacy cascade working, so the tool's description warns the agent.

**Deep.** Hard delete sits next to history because "never delete" and GDPR Art. 17 (right to
erasure) can't both hold (ADR 0004). The version chain is found with a recursive CTE walking
`superseded_by` in both directions.

<details><summary>Check: why delete the sibling fact "favourite language is Rust"?</summary>
It was learned from the same message. The message is being erased, so a fact derived from it is
data the user asked to remove.
</details>

---

## 4.4 MCP: tools for an agent

**Plainly.** MCP (Model Context Protocol) is a standard way for an agent such as Claude Code to
discover and call tools. Ours exposes `remember`, `recall` and `forget` over **stdio**: the agent
starts our program and talks to it through its input and output.

**Details that matter.**
- The tool *docstring* is what the agent reads to decide when to call it, so it is written for the
  agent (the `forget` one warns about the cascade).
- **The user is fixed by `MEMORY_USER_ID`.** Tools take no `user_id`, so an agent can't be tricked
  into reading or erasing someone else's memories.
- stdout is the protocol, so logs must go to stderr (they do).
- In `mcp` 2.x the server class is `MCPServer` (it was `FastMCP`), and tools reach shared
  resources through `ctx.request_context.lifespan_context`.
- On Windows, psycopg's async mode needs the Selector event loop, so entry points pass
  `loop_factory=asyncio.SelectorEventLoop` to `asyncio.run`.

<details><summary>Check: why not let the agent pass `user_id`?</summary>
Because the agent's input is influenced by whatever text it reads; fixing the identity in
configuration removes that attack.
</details>

---

## 4.5 A Python SDK is a thin wrapper, on purpose

**Picture it.** A TV remote: it sends signals to the TV, and holds none of the TV's logic.

**Plainly.** Instead of writing HTTP requests by hand, a Python program can do:

```python
from agentic_memory.client import Memory

mem = Memory("aman", base_url="http://127.0.0.1:8000", api_key="...")
mem.add("I moved to Pune.")
print(mem.search("Where do I live?").text)
```

**The real version.** `client.py` only builds requests and parses replies with `httpx`; all
behavior stays in the service (so there is no second place to keep correct). `add` defaults
`occurred_at` to "now", which is part of the idempotency key (ADR 0013): to retry a failed call
safely you pass the same `occurred_at` again. The test uses `httpx.MockTransport`, a fake network,
so it needs no server, database or LLM.

**Interview line.** "The SDK holds no logic: one service, three clients (REST, MCP, Python)."

<details><summary>Check: why does a retried `add` need the same `occurred_at`?</summary>
The turn's fingerprint includes it; a new timestamp looks like a brand-new message and would be stored twice.
</details>

---

## Try it yourself
1. `docker compose up -d db`, then migrate and serve (see the README). Open
   `http://127.0.0.1:8000/docs` and call `/memories:ingest` with the key from `.env`.
2. Send the same ingest twice with the same `occurred_at`. Second response: `{"steps": []}`.
3. `claude mcp add agentic-memory -- ...` (README), then ask Claude Code to remember something,
   restart it, and ask what it knows.

## Reading
- MCP spec (modelcontextprotocol.io) · FastAPI docs: Dependencies, Security
