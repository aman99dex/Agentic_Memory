# Phase 0: The landscape, and the two indexes everything rests on

## 1. What already exists (know this before an interviewer asks "why not Mem0?")

| System | Write path | Read path | Known for |
|---|---|---|---|
| **Mem0** | LLM extracts facts → top-10 similar → LLM picks ADD/UPDATE/DELETE/NOOP | Vector search over facts | The update-operations idea we borrow. **Deletes** old facts. |
| **Zep / Graphiti** | Episodes → entities and edges in a temporal knowledge graph | cosine + BM25 + graph BFS, reranked | **Bi-temporal edges** (`t_valid`/`t_invalid`). LongMemEval_s 71.2% vs 60.2% full-context (gpt-4o). |
| **Letta (MemGPT)** | The agent edits its own memory blocks through tool calls | Agent-driven search | Tiered memory, like an OS paging RAM to disk |
| **LangMem, A-MEM** | LLM rewrites / Zettelkasten-linked notes | Vector | Weaker on LoCoMo per Mem0's (disputed) table |
| **Mastra Observational Memory** (2026) | Observer/reflector compresses history into a dated log | **No retrieval**: the log stays in context | Self-reports 94.9% on LongMemEval_s |

**Our position:** Mem0's update operations + Graphiti's bi-temporal validity, *without* a
graph DB, + the LongMemEval paper's retrieval tricks. All of it in one Postgres, with
honest ablations.

**A lesson from the field: benchmark numbers are contested.** Zep and Mem0 publicly
disputed each other's LoCoMo numbers over misconfigured baselines. An audit found about
6% of LoCoMo's answer key is wrong and its judge accepts vague answers. Redis re-ran
several systems under one judge and found that published numbers often didn't
reproduce. **This is why our numbers come from scripts, with a pinned judge and
confidence intervals (ADR 0008).** It's also why LongMemEval is the primary benchmark
and LoCoMo the secondary one.

## 2. LongMemEval in one minute
- 500 questions. In the `_s` split each question comes with about 40–50 chat sessions
  (about 115k tokens) of "haystack" that contain the evidence.
- Question types: single-session (user / assistant / preference), multi-session,
  knowledge-update, temporal-reasoning. 30 questions are *abstention* (`_abs`), where the
  right answer is "I don't know".
- Judging: GPT-4o, yes/no, with a specific prompt per type.
- Reference points: GPT-4o full-context scores about 60%, but about 87–92% when given
  *only* the evidence sessions. **That 30-point gap is the whole case for a memory
  system**: retrieval quality, not model strength, is the bottleneck.

## 3. HNSW vs IVFFlat (pgvector's two ANN indexes)
**ANN** = approximate nearest neighbour. These indexes trade exact results for speed.

**IVFFlat**: cluster first, then search the nearby clusters.
- Build: k-means splits the vectors into `lists` clusters.
- Query: search only the `probes` clusters nearest to the query.
- Needs data *before* you build it (the centroids come from it), and recall degrades as
  data drifts away from the original clusters.

**HNSW**: a layered "small world" graph.
- Each vector links to about `m` neighbours. The upper layers are sparse highways and
  the bottom layer is dense.
- Query: greedy walk from the top layer down, keeping a candidate list of size
  `ef_search`.
- Builds incrementally, so the table can start empty. Better recall at the same speed,
  but more memory and slower builds.

We use HNSW (ADR 0001), because memories arrive one at a time into an initially empty
table.

**The filtered-search trap.**
`SELECT … WHERE user_id = 'u1' ORDER BY embedding <=> $q LIMIT 10`
The index returns the ~40 (`ef_search`) nearest vectors *across all users*, and only
then does the `WHERE` drop the other users' rows. You can get back 2 rows instead of 10,
silently. pgvector 0.8 fixes this with `SET LOCAL hnsw.iterative_scan = relaxed_order`.

## 4. Postgres full-text in 60 seconds
```sql
SELECT to_tsvector('english', 'I moved to Pune last March');
-- 'last':5 'march':6 'move':2 'pune':4    ← stemmed lexemes; stopwords dropped
SELECT websearch_to_tsquery('english', 'where did I move');
-- 'move'
```
- A `GIN` index on the `tsvector` column makes `@@` matching fast.
- `ts_rank_cd` scores by term frequency and proximity. It has **no IDF**: a rare word
  counts the same as a common one. Real BM25 adds IDF and length normalisation.
  ADR 0002 explains why this matters less once we fuse by rank.

## Try it yourself
1. `docker compose up -d db`, then
   `docker compose exec db psql -U mem -d mem -c "SELECT extversion FROM pg_extension WHERE extname='vector'"`.
2. Run the `to_tsvector` examples above with your own sentences. Which words disappear?
   Why does "moved" become "move"?
3. Predict, then check in Phase 1: with 100 users × 50 memories and no iterative scan,
   how many rows does a filtered `LIMIT 10` return?

## Reading (in priority order)
1. LongMemEval: arxiv.org/abs/2410.10813. Read §3 and §5; §5 is our ADR 0003.
2. Mem0: arxiv.org/abs/2504.19413. Read §2 (the update operations).
3. Zep: arxiv.org/abs/2501.13956. Read the bi-temporal section.
4. pgvector README: "HNSW", "Filtering", "Iterative Index Scans".
5. Zep's rebuttal of Mem0's LoCoMo numbers (blog.getzep.com). A lesson in how to read
   benchmark claims.
