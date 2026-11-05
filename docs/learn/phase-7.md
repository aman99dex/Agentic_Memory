# Phase 7: Usable by real people

Phases 0 to 6 built a memory that works. Phase 7 makes it something a person installs and keeps
using: in their own Claude Code, or inside their product. Why: `docs/intent/usable-product.md`.

```
  Claude Code                         your product
  ───────────                         ────────────
  SessionStart ─► context ◄─┐         SDK: memory.context() / .search() / .add()
  UserPromptSubmit ─► prompt file     │            │ HTTP, X-API-Key (tenant)
  Stop ─► store turn ─► spawn worker  │            ▼
                         │            │       REST API ─► store turn (queued)
                         ▼            │                        │
                 ┌──────────── worker (lease per user) ◄───────┘
                 │ extract + scope ─► reconcile ─► apply ─► session summary ─► profiles
                 ▼
     SQLite file (laptop)   or   Postgres + pgvector (server)      same Store interface
```

Code: `sqlite_store.py`, `postgres_store.py`, `store.py`, `db.py`, `worker.py`, `context.py`,
`cli.py`, `config.py`, `plugin/`, `examples/chat.py`. Decisions: ADRs 0019 to 0023.

**Teaching status:** written while building, before any teaching. Teach one section at a time.

---

## 7.1 Two databases behind one interface

**Picture it.** A power socket. Your laptop charger doesn't care whether the electricity comes
from a solar panel or the grid, as long as the socket is the same shape. Where it stops being
accurate: two power sources really are identical once they reach the socket. Two databases are
not, so we have to *test* that they behave the same.

**Plainly.** The rest of the code talks to a `Store`: a list of methods like `add`, `search`,
`forget`. Two classes provide them. `SqliteStore` keeps everything in one file on your laptop.
`PostgresStore` talks to a database server. Which one you get depends on one setting.

**Show it.**
```
DATABASE_URL not set             ─► SqliteStore("~/.agentic-memory/memory.db")
DATABASE_URL=sqlite:///x.db      ─► SqliteStore("x.db")
DATABASE_URL=postgresql://...    ─► PostgresStore(pool)
```
The same test, `test_supersede_keeps_history`, runs twice: once per database (the `any_store`
fixture in `tests/conftest.py`). If one store behaves differently, a test fails.

**The real version.**
- `Store` is a `typing.Protocol`: "anything with these methods counts". mypy checks both classes
  against it without them inheriting from anything.
- **Keyword search in SQLite** is FTS5, a full-text index built into SQLite, with the Porter
  stemmer ("lives" matches "living") and `bm25()` ranking. BM25 is the ranking formula search
  engines use: rare words count more, and very long documents don't win just by being long.
  Postgres' `ts_rank_cd` has no rare-word weighting, so locally we get the better formula.
- **Vector search in SQLite** is exact: load this user's vectors, multiply by the query
  vector (`matrix @ q`), sort. No index, so no "approximate" misses. It's about 1 ms per 10,000
  facts; the `ponytail:` comment says when to change that.
- **Times** are stored as text like `2026-10-01T09:00:00.000000+00:00`. Fixed width and always
  UTC, so sorting the text sorts the times.
- **Several programs, one file:** WAL mode lets readers and one writer work at once;
  `BEGIN IMMEDIATE` takes the write lock at the start of a write, so two writers queue up
  instead of deadlocking; `busy_timeout` makes a waiting writer wait instead of failing.

**In our code.** `db.py` `open_store()` picks the class. `sqlite_store.py` `migrate()` applies
`migrations/sqlite/*.sql` and records how many in `PRAGMA user_version`. `_fts_queries()` builds
the "every word" and "any word" queries, quoting each word so user input can't become syntax.

**Why it matters.** Without SQLite, using this on a laptop means Docker plus a running Postgres.
Without the shared tests, the two stores would drift apart quietly.

**Interview line.** "Locally it's one SQLite file with BM25 and exact vector search; servers use
Postgres. Both pass one test suite, which is what keeps two SQL dialects honest."
*Follow-up:* "Why not sqlite-vec?" *Answer:* "It's pre-1.0, needs `load_extension` that some
Python builds disable, and with a per-user filter it's brute force anyway. numpy was already
installed."

**Check.** Why do we store times as fixed-width UTC text in SQLite, not as local time?
<details><summary>Answer</summary>Because SQLite compares them as text. Fixed width and one
time zone make text order equal time order; mixed zones or widths would sort wrongly.</details>

---

## 7.2 Saving is a queue: return now, work later

**Picture it.** A dry cleaner. You hand over a shirt, get a ticket, and leave in ten seconds.
The cleaning happens later in the back. Where it stops being accurate: here, the "ticket" is
the turn's id, and you never have to come back for anything.

**Plainly.** Turning a conversation into facts takes several LLM calls: seconds. Nobody wants to
wait for that. So a save only writes the turn down and returns. A *worker* picks up waiting
turns and does the slow part.

**Show it.**
```
save "I moved to Pune"   ─► turns row, processed_at = NULL      (3 ms)
worker: claim lease for user "dev"
        turn 41 (Oct 1) ─► facts ─► processed_at = now
        turn 42 (Oct 3) ─► facts ─► processed_at = now
        summaries, profiles
        release lease
```
Measured: saving went from 1.4 to 6.2 seconds per turn down to 1 to 6 milliseconds.

**The real version.**
- **The database is the queue.** A waiting turn is a row with `processed_at IS NULL`. No Redis,
  no Kafka: nothing new to run.
- **A lease** is a row saying "worker X owns user U until time T". Claiming it is one upsert
  that only succeeds if the lease is free, expired, or already yours
  (`INSERT ... ON CONFLICT DO UPDATE ... WHERE expires_at < now OR holder = me RETURNING`).
  Because one database statement either happens or doesn't, two workers can't both win.
- **Why per user?** Two turns of one user processed at once could both decide "this is new"
  and both ADD the same fact. One worker per user removes that race. Different users still run
  in parallel.
- **Order:** turns are processed `ORDER BY occurred_at`, so facts change in the order they
  happened. A turn that arrives late closes the old fact at the old fact's own start
  (`greatest(at, valid_from)`), so it can't break the rule "a fact ends after it starts".
- **Failures:** a turn that throws gets `attempts + 1` and its error message; after 5 tries it
  is left alone and `agentic-memory status` shows it.

**In our code.** `service.py` `ingest()` stores and returns (`wait=True` processes on the spot).
`worker.py` `drain_user()` is the loop. The lease SQL is `claim()` in both stores.

**Why it matters.** A chat product can't add seconds per message. And the race fix came free.

**Interview line.** "The database is the queue, and a per-user lease serialises the LLM work:
that fixed latency and a duplicate-fact race in one move, with no broker."
*Follow-up:* "What if a worker dies holding the lease?" *Answer:* "It expires after 5 minutes,
and the turn it was on is still unprocessed, so the next worker redoes it; reconcile answers
NOOP for facts already stored, so redoing is safe."

**Check.** Two API servers both receive turns for the same user at the same moment. How many
workers process that user's turns, and why?
<details><summary>Answer</summary>One. Both try to claim the lease; the upsert lets only one
succeed. The other finds the lease held and moves on.</details>

---

## 7.3 Scopes: about you, or about this project?

**Picture it.** Your notebook has a page about you ("allergic to peanuts") and a tab per client
("Acme uses AWS"). You'd never write Acme's cloud on another client's tab.

**Plainly.** Every fact is either about the person everywhere, or about one project. Inside a
project you see both kinds; another project never sees this one's facts.

**Show it.**
```
"always use type hints"          scope user     namespace NULL      seen everywhere
"we deploy to Cloud Run"         scope project  namespace c:/code/shop   seen only in shop
```
Real run: a new Claude Code session in `shop` was told both; a session in another folder was
told only the type hints.

**The real version.** The extractor labels each fact `user` or `project` (one extra field in the
structured output). Search inside namespace N filters `namespace IS NULL OR namespace = N`.
Reconcile searches the same reach, so a project fact can only replace facts it could see. For
Claude Code, the namespace is the git root of the folder (`config.project_namespace`): a full
path, so two repos both called `api` stay apart.

**Why it matters.** Without scopes, a memory used across many repos turns into noise.

**Check.** You say "this project uses Go" in repo A. Can it ever be shown in repo B?
<details><summary>Answer</summary>No. It's stored with namespace A, and a search in B only sees
namespace B and the user-wide facts.</details>

---

## 7.4 Summaries and profiles, and the bug that shaped them

**Picture it.** A doctor's file: individual test results (facts), a note per visit (what
happened), and a one-paragraph summary on the cover (who this patient is).

**Plainly.** After processing, the worker writes a short note per session ("Fixed the import in
api.py; set up uv") and rewrites a short profile of you and of the project from the current
facts. A new session reads the cover summary and the latest visit notes first.

**The real version.**
- **Episode:** one row per session, `kind = 'episode'`, rewritten in place each time the session
  gets new turns. Only the new turns are sent to the model, with the summary so far.
- **Profile:** one row per scope, `kind = 'profile'`, rewritten when that scope's facts change.
- **`context`** puts them together with no LLM and no vector search: it is two lookups and a
  sort, so it runs in a hook in about 0.6 seconds.
- **`forget`** deletes the profiles too, so a forgotten fact can't live on inside a summary.

**The bug.** The first version searched summaries together with facts. A test asked "where do I
live?" and got, ranked first: *"session on 2026-01-05: the user said they live in Bangalore"*,
above the current fact "lives in Pune". A summary is a record of the past, and it repeats things
that stopped being true. The fix has three parts: summaries describe *work*, never facts about
the user; normal questions search facts only; and when the rewritten question is about past work
(`past_sessions = true`), summaries are searched on their own and listed first.

**Interview line.** "Facts are the source of truth; summaries are derived from them in the
background and kept out of fact search, because a summary from last month will happily repeat
something that's no longer true. A test caught exactly that."

**Check.** Why does `forget` delete every profile of the user, not just the forgotten fact?
<details><summary>Answer</summary>A profile is prose written from facts; the deleted fact may be
inside it. Deleting the profile guarantees it's gone; the next change rebuilds the profile from
what remains.</details>

---

## 7.5 Hooks, and why the worker is a separate process

**Picture it.** A shop that closes at 6. A cashier writes down the last order at 5:59 (fast).
If the cook only starts at 6:01, the doors are locked and the order is never made, unless the
cook works from a separate kitchen that stays open.

**Plainly.** Claude Code runs our commands at three moments: when a session starts (we print
what we remember), when you send a prompt (we save it), and when Claude finishes answering (we
save the pair). The slow part runs in a separate background process that keeps going after
Claude Code exits.

**Show it.** The first version made the Stop hook do everything in the background. A real
`claude -p` run stored nothing: the log showed the hook *started*, then nothing. Claude Code had
exited and taken the hook process with it before it even saved the turn. Now:
```
Stop hook (0.5 s):  save turn ─► start detached worker ─► exit
detached worker:    keeps running after Claude Code exits ─► facts, summary, profiles
next SessionStart:  if anything is still queued, start a worker again
```

**The real version.**
- A **detached** process has no parent console and its own process group. On Windows we also
  ask to leave Claude Code's *job object* (`CREATE_BREAKAWAY_FROM_JOB`): a job is a Windows
  container for processes, and closing it kills everything inside. Elsewhere,
  `start_new_session=True` does the same job.
- Hooks get JSON on stdin. We use only documented fields (`prompt`, `last_assistant_message`,
  `cwd`, `session_id`) and never parse the transcript file, whose format the docs say changes.
- Every hook exits 0, even on errors (they go to `~/.agentic-memory/agentic-memory.log`): a
  memory problem must never break a coding session.
- Speed: the session-start hook first took 2.2 s, because importing LangChain and telemetry came
  along with it. Splitting modules (`db.py`, `context.py`, `postgres_store.py`) so the hook
  imports only what it uses brought it to about 0.6 s.

**Interview line.** "Capture can't depend on the model remembering to call a tool, so hooks do
it. I found in a real end-to-end run that Claude Code kills hook processes on exit, so the hook
only stores the turn and hands the LLM work to a detached worker."

**Check.** Why doesn't the Stop hook just call the LLM itself and wait?
<details><summary>Answer</summary>The hook process can be killed when the session ends (and a
synchronous hook would delay every answer by seconds). Storing first makes the turn safe in
under a second; the detached worker does the rest.</details>

---

## 7.6 The product side: tenant keys and a small client

**Plainly.** A company can give each of its apps its own API key; each key only sees its own
users. And installing the SDK installs only an HTTP library, not the whole server.

**The real version.**
- A key is `am_` + 32 random bytes, shown once; only its SHA-256 hash is stored. A request's key
  resolves to a tenant, and user ids are stored as `tenant:user`. One function, `uid()`, is the
  only way from a request to a stored id, and a test checks that tenant B and the operator can't
  read tenant A's user.
- Tenant names allow only `a-z 0-9 -`, so nobody can make a tenant named `a:b` to collide with
  tenant `a`'s user `b:...`.
- Python *extras*: `pip install agentic-memory` gives httpx only (8 packages);
  `agentic-memory[server]` adds the rest.

**Check.** Why store a hash of the API key instead of the key?
<details><summary>Answer</summary>If the database leaks, hashes can't be used as keys. The
server only needs to check a key, which it does by hashing what it's given and looking that up.
</details>
