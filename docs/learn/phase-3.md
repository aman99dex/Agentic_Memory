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

## 3.4 Rerank: what we remember should reflect what mattered (ADR 0016)

**Picture it.** A bookshelf. Books you read all the time sit at eye level; books untouched for years
migrate to the top shelf in the attic. Nobody threw them away.

**Plainly.** Search gives a score to each candidate fact. We then nudge that score: facts that keep
being useful get a boost ("importance"), and facts that haven't been touched for a long time get a
small penalty ("recency decay"). The best fact after nudging goes first.

**Show it.** Two identical facts, "The user plays chess": one last recalled yesterday, one never
recalled and two years old. With a one-year half-life the old one's score is multiplied by
0.5^(730/365) = 0.25, the fresh one by 1.0. The fresh one wins (`test_a_recent_fact_outranks_an_identical_old_one`).

**The real version.** `score = fused_score × (1 + 0.5 × importance) × 0.5^(age / 365 days)`.
Importance starts at 0, rises by 0.1 each time `recall` returns the fact (capped at 1.0), and decays
by ×0.9 every night. A **half-life** is the time for a quantity to halve. RRF scores for ranks 1 and
10 differ only by about 13% (1/61 vs 1/70), so a decay factor of 0.25 can easily overturn them: that is why the weights are
config, and why Phase 5 includes a `--no-rerank` ablation to check it helps at all.

**In our code.** `rank.py` (`Rank.factor`), `MemoryStore.search(rank=..., now=...)` and `_rerank`,
`MemoryStore.touch`, `MemoryService.recall`. Importance is earned by use, not guessed by an LLM.

**Interview line.** "Importance is earned by being recalled, so it costs no LLM call, and the weights
are exposed and ablated rather than assumed."

<details><summary>Check: why measure age against the `now` we pass in, not the clock?</summary>
The evaluation replays conversations from 2023 as if asking on their dates; real-clock age would
make every fact "years old" and the test meaningless.
</details>

---

## 3.5 Forgetting that is not deleting (ADR 0016)

**Picture it.** Moving old files to an archive box instead of the shredder.

**Plainly.** A nightly job (`python -m agentic_memory.decay`) multiplies every importance by 0.9, then
marks a current fact "archived" if it is both unimportant and hasn't been recalled for a year.
Archived facts disappear from search but stay in the database and in a fact's history, and
`unarchive` brings them back. Only `forget` (Phase 4) truly erases.

**The real version.** Archiving is a `UPDATE ... SET archived_at` and search filters
`archived_at IS NULL` in one shared constant (`VISIBLE`), so no query can forget to apply it. The
risk is real: a true-but-rarely-asked fact ("allergic to peanuts") can go cold. That is why the
threshold is a year, why it is reversible, and why it is documented as a trade-off.

**Interview line.** "Forgetting archives, never deletes, so the only irreversible operation is the one the
user explicitly asks for."

<details><summary>Check: why does the decay job skip closed (superseded) facts?</summary>
They are already invisible to search; archiving them would hide nothing and lose nothing.
</details>

## Try it yourself
1. `uv run --env-file .env python -m pytest tests/test_read_path.py -q`: expect `4 passed`.
2. Call `pack` with a budget of 20, then 500, and compare what comes back.

## Reading
- Liu et al., "Lost in the Middle" (2023) · LongMemEval (Wu et al., 2024)
