# Phase 1: Storing memories and finding them again

Phase 1 built the middle of the system: the database, and the code that searches it.

```
  agent chats ──► [ WRITE: save it ] ──► DATABASE ──► [ READ: find it ] ──► agent
                     (Phase 2)          (Phase 1)        (Phase 1 + 3)
```

Every numbered section below is one idea. Each one has the same ladder: a picture, the
plain version, a real example, the deep technical version, where it lives in our code,
and an interview line. Answer the check question before opening the answer.

---

## 1.1 Two shelves: `turns` and `memories`

**Picture it.** A library. Books are the full conversations; index cards are short facts
that say "see book 42, page 3". You search the cards, but you hand the reader the page.

**Plainly.** `turns` holds the exact chat rounds. `memories` holds short facts, each
pointing at the turns it came from (`source_turn_ids`).

**Example.** Turn t1: "I just moved from Bangalore to Pune for a new job at Neoflo".
Facts: m1 "Aman lives in Pune" → t1, m2 "Aman works at Neoflo" → t1. "Where does Aman
live?" matches m1, and the agent gets t1, which also says *where from* and *why*.

**Deep.** LongMemEval (Wu et al., 2024): returning only extracted facts lowers accuracy.
Indexing facts as extra keys but returning the original turns gave **+9.4% recall and
+5.4% accuracy**. Mem0 returns facts only; we deliberately don't (ADR 0003).

**Interview.** "Facts are my search keys and my version history. Turns are what I
return, because extraction loses detail."

<details><summary>Check: why keep t1 if we already have "Aman lives in Pune"?</summary>
The original message holds context a single fact drops: the previous city, the new
job, the reason for the move.
</details>

---

## 1.2 Migrations and the `turns` table

**Picture it.** Numbered building instruction sheets. A new database follows them in
order and ends up identical to ours. A used sheet is never edited; a fix is a new sheet.

**Plainly.** A *table* is a grid: one row per item, one column per piece of information.
A *migration* is a SQL file that changes the tables. *SQL* is the language for talking
to the database.

**Our code** (`migrations/0001_init.sql`):
- `id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY`: the database numbers each row,
  and no two rows can share a number.
- `NOT NULL`: the column can't be left empty.
- `occurred_at`: when the chat happened. `created_at DEFAULT now()`: when we stored it.

**Deep.** Two timelines are called *bi-temporal*: "when it was true" and "when we
learned it". Zep tracks the same pair.

**Interview.** "Migrations are plain SQL, numbered and forward-only, so every
environment builds the same schema."

<details><summary>Check: on Oct 18 you tell the agent about a chat from Oct 1. Which date goes where?</summary>
<code>occurred_at</code> = Oct 1, <code>created_at</code> = Oct 18.
</details>

---

## 1.3 Crossing a fact out without erasing it

**Picture it.** Visa stamps in a passport. The old visa gets an end date; the new one is
on the next page.

**Plainly.** A fact is never rewritten. When it stops being true we fill in `valid_to`
and point `superseded_by` at the replacement.

**Example.**
```
 id | content                 | valid_from | valid_to   | superseded_by
  1 | Aman lives in Bangalore | 2026-01-10 | 2026-10-01 | 2
  2 | Aman lives in Pune      | 2026-10-01 | (empty)    |
```
- "Now": rows where `valid_to` is empty → Pune.
- "In March": rows where March falls between `valid_from` and `valid_to` → Bangalore.

**Deep.** The insert and the update run in **one transaction** (all or nothing).
Otherwise a crash between them leaves two current homes, or none. Mem0 deletes old facts;
we keep them, and GDPR erasure is a separate hard-delete path (ADR 0004).

**Interview.** "Updates are versioned. I close the old fact and insert the new one in a
single transaction, so there's always exactly one current value."

<details><summary>Check: you move to Mumbai on Dec 1. Which row changes, and how?</summary>
Row 2 (Pune): <code>valid_to</code> = Dec 1, <code>superseded_by</code> = 3.
</details>

---

## 1.4 Embeddings: meaning as numbers

**Picture it.** Pins on a city map. Similar meanings land close together, even with no
words in common. Our map has 384 directions instead of 2.

**Plainly.** A small model turns a sentence into 384 numbers. Comparing two lists of
numbers tells you how similar the sentences mean.

**Measured.** "where do I live?" vs "I just moved from Bangalore to Pune": similarity
**0.579**. vs "I like dark mode in my editor": **0.381**. No shared words, right winner.

**Deep.** `bge-small-en-v1.5`, 33M parameters, 384 dimensions, run on ONNX through
fastembed (no PyTorch, so the container stays small; ADR 0006). The vectors have length
1.0, so the dot product equals cosine similarity. Each row stores `embedding_model`,
because vectors from different models can't be compared.

**Our code.** `src/agentic_memory/embedder.py`: load the model once in `__init__`,
`embed()` returns one list of 384 floats per text.

**Interview.** "I chose the embedder for the deploy target and record the model name on
every vector, so a model swap is a migration, not silent corruption."

<details><summary>Check: why does vector search struggle with "what was order A-7731 about?"</summary>
Embeddings capture meaning, not exactness. "A-7731" and "A-7732" mean almost the same
thing to the model, so they land in almost the same spot.
</details>

---

## 1.5 HNSW: the map with highways

**Picture it.** To find the closest house, take the highway to the right area, then main
roads, then side streets, instead of visiting every house.

**Measured** (20,000 rows): HNSW index **2.8 ms**, reading every row **13.4 ms**. The gap
grows with the table.

**Deep.** Hierarchical Navigable Small World (Malkov & Yashunin, 2016). `m = 16` links per
point, `ef_construction = 64` effort while building, `ef_search = 40` candidates while
searching. It's approximate: it very rarely misses the true nearest. `<=>` is cosine
distance, `1 − similarity`. Each 384-number vector is 1,536 bytes.

**Interview.** "HNSW gives approximate nearest-neighbour search in roughly logarithmic
time. I use m=16 and ef_construction=64 and tune ef_search at query time."

<details><summary>Check: why HNSW over IVFFlat here?</summary>
Better recall at the same speed, and it builds incrementally into an empty table.
IVFFlat needs data first to make its clusters.
</details>

---

## 1.6 The filtered-HNSW trap

**Picture it.** A talent show of 5,000 kids from 100 classes. Only 40 may come on stage.
The judge calls the 40 best in the school, *then* sends away everyone not in class 7.
Usually nobody is left.

**Measured.** 100 users × 50 memories; asked for 10 of user7's memories: **0 rows**
without iterative scan, **10 rows** with it.

**Deep.** HNSW collects `ef_search` (40) candidates and the `WHERE user_id = …` runs
after. pgvector 0.8.0 added `hnsw.iterative_scan`: keep walking the graph until enough
rows pass the filter, up to `hnsw.max_scan_tuples` (20,000). `relaxed_order` is faster
and can return rows slightly out of order, so we sort again outside.

**Our code.** `tests/test_filtered_hnsw.py` proves the bug and the fix.
`MemoryStore.search_vector` runs `SET LOCAL hnsw.iterative_scan = relaxed_order`.

**Interview.** "Filtered HNSW applies the WHERE after the scan, so tenant queries come
back short as tenants grow. I turned on iterative scans and wrote a test that reproduces
the 0-row result."

<details><summary>Check: with iterative scan on, what does the judge do differently?</summary>
Keeps calling more kids until 10 from class 7 are on stage, or the limit is reached.
</details>

---

## 1.7 Keyword search: `tsvector` and GIN

**Picture it.** The index at the back of a textbook: word → pages.

**Measured.** "what was order A-7731 about?": vector search ranked **A-7732 first
(wrong)**; keyword search returned **only A-7731**. But "which city do I live in?" found
nothing by keywords, because "city" isn't in "Aman lives in Pune". Each one fails where
the other wins.

**Deep.** `to_tsvector('english', …)` lowercases, drops stopwords, and stems ("moved" →
"move"). Surprise: "A-7731" becomes `'-7731'` (the "A" is a stopword, the rest reads as
a signed number), so B-7731 would also match; this is logged for Phase 5. GIN is an
inverted index and exact. `ts_rank_cd` is not BM25: it has no IDF (ADR 0002).

**Our code.** `tsv … GENERATED ALWAYS AS (to_tsvector(…)) STORED` keeps the word list in
sync automatically; `CREATE INDEX … USING gin (tsv)`.

**Interview.** "Embeddings blur exact tokens: my vector search ranked A-7732 above
A-7731. So I run full-text search next to it and fuse the lists."

<details><summary>Check: what does GIN stand for, and why "inverted"?</summary>
Generalized Inverted Index. It maps word → rows, the reverse of row → words.
</details>

---

## 1.8 Reciprocal Rank Fusion

**Picture it.** Two friends rank restaurants on different scales (stars and points).
You can't add those, but you can give points by position and add the points.

**Formula.** `points = 1 / (k + rank)`, with k = 60 and rank starting at 1.

**Example.**
```
vector:  [A-7732, A-7731, A-7730]      keyword: [A-7731]
A-7731: 1/62 + 1/61 = 0.03252   ← 1st
A-7732: 1/61        = 0.01639
A-7730: 1/63        = 0.01587
```

**Deep.** Cormack, Clarke & Büttcher, SIGIR 2009. k = 60 flattens the gap between top
ranks, so agreement between retrievers beats being #1 in one. Used by Elasticsearch,
OpenSearch, Azure AI Search and Weaviate. Weakness: it ignores how confident each list
was. A tuned convex blend can beat it, which is a Phase 5 experiment (ADR 0005).

**Our code.** `src/agentic_memory/fusion.py`, 5 lines: a dict scoreboard,
`enumerate(ranking, start=1)`, add `1 / (k + rank)`, sort by points.

**Interview.** "RRF uses ranks only, so I never normalise a cosine distance against a
text-rank score."

<details><summary>Check: an id only in the keyword list, at position 1, gets how many points?</summary>
1/61.
</details>

---

## 1.9 `MemoryStore`, the librarian

**Picture it.** Nobody touches the shelves except the librarian, so the rules live in
one place.

**Methods.** `add`, `get` (only your own), `invalidate`, `supersede` (one transaction),
`search_vector`, `search_text`, `search` (both at once + RRF). `as_of` turns "now" into
"at time T": an empty `as_of` becomes `'infinity'`, so only never-closed facts are seen.

**A bug the tests caught (ADR 0011).** Switching keyword search to OR (any word) made
every "Order …" row match, and RRF tied A-7731 with A-7732. The fix: AND first, OR only
if AND finds nothing. Root cause: `ts_rank_cd` has no IDF, so a common word like "order"
counts as much as a rare one.

**Interview.** "All memory SQL is behind one store that enforces tenant scoping and
bi-temporal visibility. Tests caught OR-semantics diluting exact IDs, so I do AND with
an OR fallback."

<details><summary>Check: why must <code>supersede</code> be one transaction?</summary>
A crash between the two steps would leave two current homes, or none.
</details>

---

## 1.10 Async and the connection pool

**Picture it.** A waiter who takes table 2's order while table 1's food cooks. The pool
is 5 phone lines to the kitchen, kept open and reused.

**Measured.**
```
open a new connection x10:      432 ms     borrow from pool x10:  44 ms
10 queries of 0.2 s, one by one: 2114 ms   with gather:          424 ms  (pool of 5)
```

**The rule.** Time ≈ (queries ÷ pool size, rounded up) × time per query. Picture an
elevator that carries 5 people per 0.2 s trip: 15 people = 3 trips = 0.6 s.

**Deep.** Each Postgres connection is an operating-system process, which is why tools
like PgBouncer exist. asyncio is one thread with an event loop; `await` means "pause me,
serve someone else". CPU work such as embedding can't pause, so `_embed` runs it with
`asyncio.to_thread`. On Windows psycopg's async mode needs `SelectorEventLoop`.

**Interview.** "Hybrid search runs both queries concurrently on a connection pool, and
the CPU-bound embedding runs off the event loop."

<details><summary>Check: pool of 5, 25 queries of 0.2 s with gather?</summary>
25 ÷ 5 = 5 trips × 0.2 s = 1 s.
</details>

---

## 1.11 The migration runner

**Picture it.** A checklist on the wall of a building site: finished sheets get their
name and a fingerprint written down. Done sheets are skipped. A mismatched fingerprint
means someone edited a finished sheet, and work stops.

**Trace.**
- First run: `applied: 0001_init.sql`.
- Second run: `nothing, already up to date`.
- A broken file is rolled back completely and is not recorded.
- An edited, already-applied file is refused.

**Deep.** The fingerprint is SHA-256. Postgres can roll back `CREATE TABLE` (transactional
DDL); MySQL can't. `CREATE INDEX CONCURRENTLY` can't run in a transaction, so it will
need a special path later. Run as a deploy step, never at app startup, so several app
copies don't race (ADR 0010).

**Why `0001` and not `1`?** Files are sorted **as text, one character at a time**, like
words in a dictionary. As text, `"10"` comes before `"9"` because `"1"` comes before
`"9"`, exactly as "apple" comes before "banana" whatever their lengths. So `1, 2, … 9, 10`
would sort as `1, 10, 2, …, 9`, and migration 10 would run before migration 2. Padding
to the same width (`0002`, `0009`, `0010`) makes text order equal number order.

**Interview.** "Forward-only SQL migrations, applied in order and transactionally, with
a SHA-256 checksum per file, so an edited migration fails loudly."

<details><summary>Check: which runs first if the files are named <code>2_add.sql</code> and <code>10_fix.sql</code>?</summary>
<code>10_fix.sql</code>, because as text "1" comes before "2". That's the bug zero-padding prevents.
</details>

---

## Try it yourself

1. Start Docker Desktop. In PowerShell:
   `cd C:\Users\kumar\Desktop\Agentic_Memory` then `docker compose up -d db`.
2. Apply the migrations:
   `$env:DATABASE_URL="postgresql://mem:mem@localhost:5432/mem"; uv run python -m agentic_memory.migrate`.
   Expect `applied: 0001_init.sql` (or `nothing, already up to date`).
3. Run every test: `uv run pytest -q`. Expect `17 passed`.
4. **Break it on purpose:** in `src/agentic_memory/fusion.py` change `start=1` to
   `start=0`, then run `uv run pytest tests/test_rrf.py -q`. Do the tests still pass?
   Think about why, then change it back.
   <details><summary>Hint</summary>Starting at 0 adds the same shift to every rank. Look at whether the order can change, rather than the exact points.</details>
5. **Break it on purpose:** in `tests/test_filtered_hnsw.py` change `PER_USER = 50`
   to `PER_USER = 5000` (500,000 rows, so it takes a while). Does iterative scan still
   return 10?

## Reading
- pgvector README: "Iterative Index Scans" and "Filtering".
- Cormack, Clarke & Büttcher (2009), *Reciprocal Rank Fusion outperforms Condorcet and
  individual Rank Learning Methods*. Two pages.
- PostgreSQL docs, chapter 12 "Full Text Search", sections 12.3 (parsing) and 12.3.3
  (ranking).
- Malkov & Yashunin (2016), *Efficient and robust approximate nearest neighbor search
  using HNSW graphs*, sections 1 and 4.
