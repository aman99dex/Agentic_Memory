# 0011. Keyword query: all words first, any word as a fallback

**Status:** Accepted, 2026-10-18

## Context
Users ask questions in full sentences, and Postgres' `websearch_to_tsquery` joins the
remaining words with AND. Two cases from Phase 1 tests pulled in opposite directions:
- **AND misses paraphrases.** "which city do I live in?" becomes `'citi' & 'live'`, and
  "Aman lives in Pune" has no "city", so the keyword side returns nothing.
- **OR loses precision.** "what was order A-7731 about?" becomes `'order' | '-7731'`, and
  every order matches "order". The keyword list became [A-7731, A-7732, A-7730], and
  RRF tied A-7731 with A-7732 (1/62 + 1/61 against 1/61 + 1/62), because vector search
  had already put A-7732 first. `ts_rank_cd` did score A-7731 higher, but RRF only uses
  ranks (ADR 0005), and `ts_rank_cd` has no IDF to discount a common word like "order"
  (ADR 0002).

## Options
| Option | For | Against |
|---|---|---|
| AND only | Precise on IDs and names | Long questions almost never match every word |
| OR only | Always finds something | Common words flood the list and dilute exact matches in RRF |
| **AND, then OR if AND is empty** | Precise when possible, recall when not | A second query on a miss; still no IDF on the OR path |
| BM25 (IDF built in) | The principled fix for common words | Not available on Neon (ADR 0002) |

## Decision
Run the AND query. If it returns no rows, run the OR query. Both are ranked with
`ts_rank_cd`. The tests `test_hybrid_fixes_what_vector_search_gets_wrong` and
`test_keyword_side_matches_any_word` pin both cases.

## Trade-off
On a miss, the second query costs one extra round trip, a few milliseconds. The OR path
still has no IDF, so a long question that misses on AND can still be diluted by common
words. **Phase 5 ablation:** AND-only vs OR-only vs fallback vs `pg_textsearch` BM25 on
the dev sample.

**In an interview:** "Strict AND kept IDs precise but missed paraphrased questions; OR
found them but flooded RRF with every row containing a common word. I do AND first and
fall back to OR, the classic search-engine pattern, and measure it against BM25."
