# Phase 2: The write path (turn in, facts out)

Phase 1 built the database and the search. Phase 2 fills it: every time a user chats, a
small pipeline turns that round of conversation into facts.

```
 turn ─► add_turn ─► extract ─► reconcile ─► apply
         (store the  (LLM lists  (per fact: LLM    (code runs the
          raw turn)   facts)      picks a label)     SQL for it)
```

Code: `src/agentic_memory/write_path.py` (graph) and `store.py` (`add_turn`,
`turn_has_memories`). Tests: `tests/test_write_path.py` (real Postgres, real LLM).

**Teaching status:** these notes were written while building, ahead of any teaching
(Aman asked for build first, teach last, 2026-10-26). Teach from them in order, one
section at a time, and ask each check question.

---

## 2.0 The big picture: the LLM decides, code acts

**Picture it.** A contacts app. A friend texts "Ravi moved to Pune". An assistant reads it,
looks at Ravi's card ("lives in Bangalore") and says "replace the city". The app makes the
edit and keeps the old city crossed out. Where it stops being accurate: a real assistant
sometimes gets things wrong, so we only accept an answer from a short fixed list.

**Plainly.** The LLM never writes SQL and never chooses what runs next. It picks one of four
labels. Plain `if` statements in our code do the rest.

| Label | Means | What the code does |
|---|---|---|
| `ADD` | new information | insert a fact |
| `UPDATE` | a new truth replaces an old fact | insert the new fact, close the old one |
| `INVALIDATE` | an old fact ended, nothing replaces it | close the old fact |
| `NOOP` | nothing new | do nothing |

**Deep.** Mem0 has the same four operations but its fourth is DELETE; we close the old row
(`valid_to`) instead, so "where did the user live in March?" still works (ADR 0004).

**Interview.** "The LLM classifies, deterministic code executes. The model's output space is
four labels, so a bad answer has a bounded blast radius and is testable."

<details><summary>Check: "Ravi still lives in Pune" and Pune is already stored. Which label?</summary>
`NOOP`: it says nothing new.
</details>

---

## 2.1 Idempotency: the same turn twice is stored once

**Picture it.** Posting the same letter twice, but the post office stamps each letter with a
fingerprint and refuses a second letter with the same fingerprint.

**Plainly.** Networks retry. If the same chat round arrives twice we must not store it twice.
We compute a SHA-256 fingerprint of the round and put a **unique index** on
`(user_id, content_hash)`. The database refuses the duplicate.

**Example.** "ok thanks" at 10:00 and again at 10:05 in session `s1` are two rows, because
`occurred_at` is part of the fingerprint. The same message re-sent with the same timestamp is
one row.

**Deep.** The fingerprint is `json.dumps([session, time, user_text, assistant_text])`, not
string concatenation, so `("ab","c")` and `("a","bc")` hash differently. The insert is
`INSERT ... ON CONFLICT DO NOTHING RETURNING id`. If it returns no row, a **second statement**
fetches the existing id: one SQL statement sees one snapshot, so a single combined statement
can miss a row a concurrent request just committed. Check-then-insert in Python would race;
the unique index cannot.

**Interview.** "Idempotency is enforced by a unique constraint, not by application code, so it
holds under concurrent retries."

<details><summary>Check: why not `SELECT` first, then `INSERT` only if missing?</summary>
Two requests can both SELECT, both see nothing, and both INSERT. The unique index is the only
thing that serializes them.
</details>

---

## 2.2 LangGraph: state, nodes, edges

**Picture it.** A factory line. A tray (the **state**) moves along a conveyor. Each station
(a **node**) reads the tray, adds something to it, and passes it on. Signs on the conveyor
(**edges**) say where it goes next; some signs ask a question first (**conditional edge**).

**Plainly.** `State` is a typed dictionary. A node is a function that returns *only the keys it
changed*; LangGraph merges them in. Our graph:

```
START ─► extract ─?─► reconcile ─?─► apply ─► END
              │ no facts        │ all NOOP
              └────► END        └────► END
```

**In our code.** `build_graph` registers three nodes and two `add_conditional_edges`. The
routing functions `has_facts` and `has_actions` are ordinary Python: the LLM output is a label
in the state, and *our* function reads it. That is what "edge followed deterministically"
means.

**Deep.** Nodes are `async` so Postgres and the LLM calls don't block each other. LangGraph
1.x compiles with `StateGraph(State).compile()`. We use no checkpointer yet: a turn is short
and re-running it is safe (2.6), so persisted graph state would add moving parts for nothing.

**Interview.** "I used a graph so each step is separately testable and the routing is code,
not a prompt."

<details><summary>Check: what does a node return?</summary>
A dict of just the state keys it changed, e.g. `{"facts": [...]}`.
</details>

---

## 2.3 Extract: structured output

**Plainly.** One LLM call reads the turn and returns a list of short, self-contained
sentences ("The user lives in Pune."). We don't parse free text: `with_structured_output(Facts)`
makes the model fill a Pydantic schema, and Pydantic rejects anything that doesn't fit.

**Deep.** The prompt carries the conversation's date so "last week" becomes an absolute date.
Rules that came from watching real output: one fact per change (never also a negation of the
old fact), and never the same fact twice with a date added. `init_chat_model` reads the model
from `EXTRACT_MODEL` (ADR 0007, 0012). Haiku 5.5 rejects the `temperature` parameter, so we
don't send one.

**Interview.** "Structured output turns the LLM into a typed function: bad shapes fail
validation instead of corrupting the database."

<details><summary>Check: why include the turn's date in the prompt?</summary>
So relative dates ("I quit last week") become absolute and stay true when read later.
</details>

---

## 2.4 Reconcile: compare to what we already know

**Picture it.** Before filing a new card, the clerk pulls the five most similar existing cards
and asks "does this replace one, duplicate one, or stand alone?"

**In our code.** For each new fact: `store.search(...)` (the hybrid search from Phase 1) returns
the top 5 *currently valid* facts. The LLM sees them numbered `[id] text` and returns a
`Decision(action, memory_id)`. **It never rewrites the fact text**; the stored text is the
extractor's, so the judge can't smuggle in a paraphrase.

**The guard (`_check`).** A label is not trusted blindly. If the LLM names a `memory_id` it was
not shown, `UPDATE` becomes `ADD` (still new information) and `INVALIDATE` becomes `NOOP`
(closing nothing is nothing). This blocks hallucinated or cross-tenant ids.

**Interview.** "The judge sees a few candidates and may only point at one of them. Code
verifies the pointer before acting."

<details><summary>Check: why is an unknown id on UPDATE downgraded to ADD, not dropped?</summary>
The fact is still real information about the user; only the claim that it replaces something
was bad.
</details>

---

## 2.5 Apply: code runs the SQL

`apply` loops over the decisions: `ADD` → `store.add`, `UPDATE` → `store.supersede` (insert the
new row and close the old one in **one transaction**, so there is never a moment with zero or
two current answers), `INVALIDATE` → `store.invalidate`. A new fact's `valid_from` is the
turn's `occurred_at`, not "now", so back-filled history is dated correctly.

**Known ceiling (`ponytail:` comment).** Turns must arrive in time order: a fact dated before
the one it replaces trips the `valid_to >= valid_from` CHECK. For bulk import, sort by
`occurred_at` first.

<details><summary>Check: why one transaction for UPDATE?</summary>
If the insert succeeded and the close failed, two contradictory facts would both be "current".
</details>

---

## 2.6 Crash safety: retrying a half-finished turn

**Plainly.** `add_turn` stores the raw turn first, then the graph runs. If the process dies in
between, the turn exists with no facts. A naive "already stored, skip" would lose it forever.

**In our code.** `ingest_turn` skips a repeat **only if** `turn_has_memories` is true. A repeat
with no facts is re-run. That is safe because reconcile answers `NOOP` for facts already
stored. Cost: a turn that genuinely has no facts ("ok thanks") is re-sent to the LLM when
repeated. Upgrade path if that matters: a `processed_at` column.

<details><summary>Check: what makes re-running a half-done turn safe?</summary>
Reconcile compares to stored facts, so facts that already landed come back as `NOOP`.
</details>

---

## 2.7 What the real data taught us (prompt fixes)

Ran seven turns of Aman's life through Haiku 5.5 and read every decision. Four real flaws,
each fixed in the prompts:

| Saw | Why | Fix |
|---|---|---|
| "eats chicken" → `INVALIDATE`; the new fact was never stored | `INVALIDATE` stores nothing | `UPDATE` = a new truth replaces an old one; `INVALIDATE` = only an end |
| "no longer vegetarian" *and* "eats chicken" both extracted | double extraction | one fact per change |
| "works at Acme" and "started at Acme in October" both stored | restating with a date | say each thing once |
| "is moving to Pune" stayed current after "lives in Pune" | plan vs outcome not linked | `UPDATE` also when a stored plan has now happened |

**Lesson:** a prompt is code. Read the real decisions, not just the pass/fail test.

---

## Try it yourself
1. Start Docker Desktop, then in the repo run
   `uv run --env-file .env python -m pytest tests/test_write_path.py -q`. Expect `2 passed`.
2. In `write_path.py`, delete the `_check` guard and make the judge return `memory_id=9999`.
   What happens to the facts? (Answer: without the guard `supersede` raises `LookupError`.)
3. Ingest the same turn twice in a REPL and compare the row counts.

## Reading
- Mem0 paper (extract + update operations) · LangGraph docs: Graph API · LongMemEval (Wu et al., 2024)
