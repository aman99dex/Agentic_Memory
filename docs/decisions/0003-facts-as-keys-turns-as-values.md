# 0003. Index extracted facts, return the source turns

**Status:** Accepted, 2026-10-16. **Changes the original plan.**

## Context
The original plan stored only LLM-extracted facts and returned them. The LongMemEval
paper (Wu et al., 2024, §5) tested this directly:
- Using **only** extracted facts or summaries as the retrieved value generally **lowers**
  QA accuracy, because extraction loses detail. Multi-session reasoning is the exception.
- **Fact-augmented key expansion** works better: index the raw text under *extra keys*
  made from facts extracted from it, but return the raw text. Averaged over models this
  gave **+9.4% recall and +5.4% accuracy**.
- **Round granularity** (one user turn + assistant reply) beats whole sessions as the
  unit.

Mem0 returns only facts, so this is a deliberate place where we differ from Mem0.

## Decision
Two tables:
- `turns`: raw rounds (user + assistant), with timestamp and session id. These are the
  *values*.
- `memories`: extracted facts, each with `source_turn_ids`. These are *keys* for search,
  and the *state* that gets updated, invalidated and versioned.

At read time both tables are searched. Fact hits expand to their source turns, and the
packer decides how much budget goes to facts (compact, current) versus turns (detailed,
original wording).

## Trade-off
More storage and a slightly more complex packer. In return we can A/B the paper's result
on our own system: `return=facts` vs `return=turns` vs `return=both`.

**In an interview:** "The LongMemEval authors showed that returning only extracted facts
loses detail. I use facts as search keys and as the versioned state, but return the
original turns. I re-ran their ablation on my system."
