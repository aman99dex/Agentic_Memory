# 0005. RRF (k=60) first, convex fusion as an experiment

**Status:** Accepted, 2026-10-16

## Context
We have two ranked lists, vector and lexical, whose scores aren't comparable: cosine
distance vs `ts_rank_cd`. We need one ranking.

## Options
- **Reciprocal Rank Fusion** (Cormack, Clarke & Büttcher, SIGIR 2009):
  `score(d) = Σ 1 / (k + rank_i(d))`, with k=60. It uses ranks only, needs no labels and
  no score normalisation.
- **Convex combination**: `α·norm(lex) + (1-α)·norm(vec)`. Bruch et al. (TOIS 2023)
  found it **beats RRF** in- and out-of-domain once α is tuned on a few labelled
  examples. It is also more sensitive to the normalisation.

## Decision
Ship RRF as the default, because on day 1 we have no labelled data. Once the eval set
exists (Phase 5), run **convex fusion with α tuned on a dev split** as an A/B variant,
evaluated on a held-out split. Tuning α on the test split would be cheating.

## Why k=60
k dampens how much the top ranks dominate. At k=60, rank 1 scores 1/61 and rank 10
scores 1/70, so ranks are close and agreement *between* retrievers matters more than
being #1 in one. It is a convention that has held up well across collections, not a
magic number, so Phase 5 includes a small k sweep.

**In an interview:** "RRF is the label-free default. The literature says a tuned convex
combination beats it, so once I had an eval set I tested that as a variant with a proper
dev/test split."
