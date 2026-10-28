# Phase 6: Production polish

A system that works on your laptop is a demo. Phase 6 is what makes it something you can run,
watch, load, and ship.

```
 code ─► CI (lint, types, tests, image builds) ─► build image ─► migrate (job) ─► deploy (Cloud Run)
                                                                      │
 request ─► API ─► spans + token counters ─► any OTLP backend         └─ Neon Postgres
```

Code: `telemetry.py`, `loadtest/locustfile.py`, `Dockerfile`, `docker-compose.yml`,
`.github/workflows/deploy.yml`, `docs/deploy.md`. Decisions: ADR 0009, 0018.

**Teaching status:** written while building, before any teaching. Teach one section at a time.

---

## 6.1 Traces and metrics: seeing inside a running system

**Picture it.** A parcel with a tracking page. Each scan ("left depot", "out for delivery") is a
**span**: it has a name, a start, an end. All scans of one parcel form a **trace**.

**Plainly.** When a request is slow, logs say "something took 3 s" but not *where*. Tracing records
each step as a span, nested under the request. For one `ingest` you see:

```
ingest ───────────────────────────────────────────────  2.4 s
 ├─ extract ───────────────  1.1 s   (LLM call)
 ├─ reconcile ─────────────  1.0 s   (LLM call per fact + a search)
 └─ apply ─  0.2 s                   (SQL writes)
```

**Metrics** are numbers over time: a **counter** only goes up (tokens used), so a dashboard can
turn it into "tokens per minute".

**The real version.**
- **OpenTelemetry (OTel)** is the vendor-neutral standard. Our code uses only the *API*
  (`tracer.start_as_current_span`, `counter.add`). The *SDK* and exporter are switched on only when
  `OTEL_EXPORTER_OTLP_ENDPOINT` is set; otherwise every call is a no-op. Backends (Jaeger, Grafana,
  Honeycomb) all accept **OTLP**, the wire format.
- Token counts come from each model response's `usage_metadata`. Because
  `with_structured_output` returns only the parsed object, we attach a **callback** to the model
  itself (`UsageCallback`) to see the raw response.
- Cost = tokens × price, but the prices come from env vars, because we won't hard-code a number
  that goes stale.
- **No message text in any attribute.** Memories are personal data; traces are shipped to third
  parties and kept for weeks.

**In our code.** `telemetry.py` (`init_telemetry`, `traced`, `UsageCallback`); spans in
`write_path.py` (`ingest` and one per node) and `read_path.py` (`recall.*`).

**Interview line.** "OTel API in the code, SDK only when configured, spans per graph node, token and
cost counters, and nothing personal in telemetry."

<details><summary>Check: why wrap each graph node, not just the whole ingest?</summary>
The whole-ingest span says "slow"; the node spans say which step (LLM extraction vs SQL) is the
reason.
</details>

---

## 6.2 Percentiles and load testing

**Picture it.** A coffee shop. The *average* wait is 2 minutes, but one customer in twenty waits 10.
The average hides them; the **p95** ("95 out of 100 customers were served within X") shows them.

**Plainly.** Users feel the slow tail, so we report p50 (typical) and p95 (the bad day), not the
mean. A **load test** sends many requests at once to see how those numbers move as traffic grows.
`locust` simulates users: each runs a script (search, search, search, ingest...) with short pauses.

**Show it.** `python -m locust -f loadtest/locustfile.py --headless -u 50 -r 5 -t 2m`:
50 users, adding 5 per second, for 2 minutes. At the end the file `eval/results/<LOAD_NAME>.json`
holds p50/p95/throughput per endpoint, and `python -m eval.report <name>` prints the table.

**The real version.**
- Our requests are of two kinds: those that call an LLM (ingest, rewritten search) and those that
  don't (`search (no rewrite)`: embed the query, ask Postgres). The plan's "p95 under 150 ms" is
  about the second kind; an LLM round trip alone exceeds it. So we *report them separately*
  (ADR 0018), instead of blending them into one flattering number.
- **Closed-loop caveat** (the "coordinated omission" problem, Gil Tene): a locust user waits for its
  response before sending the next request, so when the server stalls, fewer requests are sent
  during the stall and the tail looks better than real traffic would. State that limit when quoting p95.
- Each simulated user is its own tenant, so the test also exercises per-tenant isolation.

**Interview line.** "I report p95 per endpoint class: retrieval on its own, and the LLM-bound paths next to
it, with the closed-loop caveat stated."

<details><summary>Check: your average latency is great but your p95 is terrible. What does it mean?</summary>
Most requests are fast, but a meaningful minority are slow (cold cache, lock contention, a slow
LLM call). Users remember the slow ones.
</details>

---

## 6.3 Containers: ship the machine, not instructions

**Picture it.** A shipping container: whatever is inside, every crane and truck handles it the same.

**Plainly.** A **Docker image** is a frozen copy of the app and everything it needs (Python,
libraries, the embedding model). `docker compose up` starts the database, runs the migrations once,
then starts the API, with no installation on your side.

**The real version (read `Dockerfile` top to bottom).**
- **Layers and cache.** Each instruction is a layer. We copy `pyproject.toml` and `uv.lock` and
  install dependencies *before* copying the source, so editing code doesn't reinstall libraries.
- `uv sync --locked` fails if the lockfile is out of date, so the image can only contain versions
  you actually tested.
- **The embedding model is downloaded at build time** (`Embedder()` runs once), so a cold start
  doesn't spend seconds fetching ~130 MB.
- **Non-root user:** if someone breaks into the app, they are not root in the container.
- **Migrations are a separate step** (compose service `migrate`, a Cloud Run job in production),
  never app startup (ADR 0010): two app copies starting at once must not race to change the schema.

**Interview line.** "One image runs the API, the migration job and the decay job; the model is baked
in so cold starts don't download it."

<details><summary>Check: why copy the lockfile before the source code?</summary>
Docker reuses a cached layer if its inputs are unchanged. Dependencies change rarely, source
changes constantly; this order keeps the slow install layer cached.
</details>

---

## 6.4 CI/CD without a secret key (OIDC)

**Picture it.** A hotel key card that works for one hour, issued at the desk after the desk
confirms who you are, versus a metal key you could lose forever.

**Plainly.** **CI** runs checks on every push. **CD** deploys on success. Deploying needs
permission to Google Cloud. The old way: store a long-lived JSON key in GitHub (if leaked, it works
until someone notices). The new way, **Workload Identity Federation**: GitHub gives the job a signed,
short-lived token that says "I am a run of repo X on branch main", and Google swaps it for a temporary
credential, but only if X is the repo you allowed.

**The real version.** `permissions: id-token: write` lets the job request a GitHub OIDC token (a
signed JWT with claims such as `repository` and `ref`). Google's Security Token Service checks the
signature and your pool's condition (`attribute.repository == "<owner>/<repo>"`) and returns an
access token for the service account. Nothing long-lived is stored.

**Pipeline order (`deploy.yml`):** build image → run migrations as a Cloud Run **job** → deploy
the service. If the migration fails, the deploy never starts, so old code keeps serving.
It starts only after the `ci` workflow succeeds on `main`.

**Interview line.** "Deploys use OIDC federation, so there is no key to leak, and migrations run as a gate
before traffic moves."

<details><summary>Check: why restrict the Workload Identity Pool to one repository?</summary>
Otherwise any GitHub repo could present a valid GitHub token and borrow your cloud permissions.
</details>

---

## 6.5 Try it yourself

1. `docker compose up --build` then `curl localhost:8000/docs -H "X-API-Key: change-me"` (needs
   Docker Desktop running and a filled `.env`).
2. Set `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318`, run a local collector (for example
   Jaeger all-in-one), ingest a message, and look at the `ingest` trace.
3. `uv sync --group load`, then run the locust command in 6.2 against a running API.
4. Read `docs/deploy.md` and list which steps only you can do (cloud accounts).

**Reading.** OpenTelemetry docs (Python); *Google SRE Book*, "Monitoring Distributed Systems"; Gil Tene,
"How NOT to Measure Latency"; the GitHub docs on OIDC; Docker's guide to layer caching.
