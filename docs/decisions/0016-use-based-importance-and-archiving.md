# 0016. Importance comes from use; rerank by importance and recency; forgetting archives, never deletes

**Status:** Accepted, 2026-10-29. Implements the Phase 3 items in plan.md; builds on ADR 0004.

## Context
The plan asks for `score = rrf * (1 + importance) * recency_decay(last_accessed_at)` and a nightly
job that decays importance and archives memories that are cold and unimportant. Two things were
left open: where "importance" comes from, and how to forget without breaking ADR 0004 (history is
kept; only `forget` hard-deletes).

## Options for importance
1. **An LLM scores each fact at extraction time** (as in the Generative Agents paper). One more
   field in the extraction schema, so every cached extraction in the eval is invalidated, and the
   number is the model's guess about the future.
2. **Importance comes from use.** A fact starts at 0, and every time recall returns it, importance
   rises by `access_boost` (0.1, capped at 1.0). A nightly job multiplies it by 0.9.
3. **A hand-set field on the API.** Nobody will fill it in.

## Decision
- **Option 2.** It costs no LLM call, needs no new prompt, leaves cached extractions valid, and
  is observable: a fact that keeps being useful is, by definition, important.
- **Rerank (`rank.py`, `MemoryStore.search(rank=...)`):** the fused RRF score is multiplied by
  `1 + importance_weight * importance` (weight 0.5) and by `0.5 ** (age / half_life)` (365 days).
  Age is measured from `last_accessed_at`, or from `valid_from` if the fact was never recalled,
  against the `now` passed in, so the evaluation can replay history at the benchmark's dates.
  Candidates are the top 20 of each retriever; only the final top 10 are returned. All four
  numbers are config (`RANK_*` env vars). A weight of 0 switches a term off, and the evaluation's
  `--no-rerank` ablation uses exactly that.
- **Recalls leave a trace only through the service** (`MemoryService.recall` calls
  `store.touch`). The evaluation never touches, so reads stay side-effect free there.
- **Forgetting (`decay.py`, nightly):** importance *= 0.9; then a current fact with importance
  at or below 0.05 that was last recalled (or created) over 365 days ago gets `archived_at`.
  Archived facts are excluded from search (`VISIBLE`) but remain in the table and in `history`;
  `MemoryStore.unarchive` restores them. Closed (superseded) facts are never archived: they are
  already out of search.
- Migration 0004: `importance`, `last_accessed_at`, `archived_at`.

## Trade-off
- Recency decay can bury an old fact that is still true and rarely asked about ("allergic to
  peanuts"). The 365-day half-life and the importance boost are the mitigations, and the ablation
  (Phase 5) measures whether rerank helps at all; the default stays on only if it does.
- Archiving hides a fact that is current but cold. That is the plan's behavior, kept
  conservative (a year cold, unimportant) and reversible. There is no REST route for
  `unarchive` yet.
- Importance has a feedback loop: facts that rank high get recalled, so they rank higher. The cap
  at 1.0 (a 1.5x boost at weight 0.5) bounds it.

**In an interview:** "Importance isn't a guess from an LLM; it's earned by being recalled, so it
costs nothing and can't be wrong about the future. Forgetting archives instead of deleting, so
the only irreversible operation in the system is the explicit user-requested `forget`."
