# Phase 3: The read path (question in, a packed answer out)

Phase 2 fills the database. Phase 3 reads it back for the agent:

```
 question ─► rewrite ─► hybrid search ─► expand to turns ─► pack to a budget ─► text
             (LLM)      (Phase 1)        (ADR 0003)         (no LLM)
```

Code: `src/agentic_memory/read_path.py`, `store.get_turns`. Tests: `tests/test_read_path.py`.

**Teaching status:** written while building, before any teaching (build first, teach last).
Teach one section at a time and ask each check question.

---

## 3.1 Rewrite the question so it can stand alone

**Picture it.** Someone hands the librarian a note that says "what did she say about it?".
The librarian can't search "she" or "it". First they ask: "who, and about what?", and rewrite
the note as "What did Priya say about the Pune job?".

**Plainly.** Search matches words and meaning. A question that leans on earlier conversation
("she", "that", "it") has nothing to match. One cheap LLM call rewrites it using the last few
messages the caller passes in `context`.

**Example.** Context: "Priya told me she's leaving". Question: "when does she go?" becomes
`search_text = "When does Priya leave?"`.

**Deep.** The same call also pulls out a **time**: "where did I live in March?" returns
`as_of = 2026-03-...`, which feeds the `as_of` filter from Phase 1 so only facts that were
true then are searched (ADR 0004). "Now" and "currently" give `null`. The model returns a
bare date, so code attaches UTC before it reaches Postgres. Uses the `QUERY_MODEL` role
(ADR 0007).

**Interview.** "Retrieval can't resolve pronouns, so I resolve them before searching, and the
same call extracts the time the question is about."

<details><summary>Check: why does the LLM only return a date, not run the search itself?</summary>
Same rule as the write path: the LLM produces a typed value, our code does the work.
</details>

---

## 3.2 Return the turns, search the facts (ADR 0003)

**Plainly.** Facts are short and easy to match, so we search them. But a fact drops detail
("lives in Pune" loses "moved from Bangalore for a job"). So after finding facts we fetch the
**turns they came from** (`source_turn_ids`) and hand those back too. `mode` can be `facts`,
`turns` or `both`, so Phase 5 can measure which is better.

**Deep.** LongMemEval showed that facts as search keys with original turns as values beat
facts only. Turns are deduplicated by id (`dict.fromkeys` keeps first-seen order), so two facts
from the same turn don't print it twice. `get_turns` filters by `user_id`, so a bad id can
never leak another user's text.

<details><summary>Check: two facts came from turn 7. How many times does turn 7 appear?</summary>
Once.
</details>

---

## 3.3 The budget packer

**Picture it.** Packing a suitcase with a weight limit: the most important things go in
first, and you stop when the next item would not fit.

**Plainly.** The model's context is limited and costs money, so `recall` takes a `budget` in
tokens. `pack` writes the facts first (short, current), then the source turns, in rank order,
until the budget is used.

**In our code.** `_tokens` estimates 4 characters per token (marked `ponytail:`; swap in a real
tokenizer if budgets must be exact). Within a list, packing stops at the first item that doesn't
fit, so a lower-ranked item never jumps ahead of a higher one; leftover budget still goes to the
next list. Superseded facts never appear because search only returns currently-true facts
unless `as_of` is set.

**Deep, and not yet done.** "Lost in the middle" (Liu et al., 2023): models use information at
the start and end of a long prompt better than the middle. Ordering matters, and we currently
put best-ranked first. Phase 5 can test putting the best at both ends.

<details><summary>Check: budget is 50 tokens and the first fact costs 200. What is returned?</summary>
No facts. Turns are tried next with the same remaining budget; if one fits it is included,
otherwise the result is empty.
</details>

---

## Not built yet (on purpose)
- **Rerank** by importance and recency, and **forgetting by decay**: both need new columns
  (`importance`, `last_accessed_at`). They are worth building once Phase 5's evaluation shows
  whether plain RRF ranking is the bottleneck, rather than before.

## Try it yourself
1. `uv run --env-file .env python -m pytest tests/test_read_path.py -q`: expect `4 passed`.
2. Call `pack` with a budget of 20, then 500, and compare what comes back.

## Reading
- Liu et al., "Lost in the Middle" (2023) · LongMemEval (Wu et al., 2024)
