# 0015. How the LongMemEval harness is built and what it can claim

**Status:** Accepted, 2026-10-27. Implements ADR 0008; refines the judge rule in ADR 0007.

## Context
Phase 5 compares three systems on LongMemEval_s: the whole history in the prompt (`full`),
chunked vector search (`rag`), and this project (`ours`). Each question carries about 48
sessions and 250 conversation rounds, so choices about cost, fairness and judging decide
whether the numbers mean anything.

## Decisions
1. **Official judge prompts, judge model configurable.** The prompts are copied verbatim from
   the benchmark's `evaluate_qa.py` (`eval/judge.py`, with its URL), and the "yes in reply"
   rule is the same. The official judge model (gpt-4o-2024-08-06) needs an OpenAI key we don't
   have, so `JUDGE_MODEL` defaults to Claude Sonnet 5.5. Every results file and README table
   states `judge_is_official: false`. **These numbers are not comparable to published
   leaderboard numbers**; they are comparable across our own variants.
2. **Every LLM call is cached by (model, schema, prompt)** in SQLite (`eval/llm_cache.py`).
   Reruns and ablations that repeat a call cost nothing, results are reproducible, and the
   cache also caps calls in flight.
3. **Ingestion goes through the real write path, extraction in parallel.** Extraction doesn't
   depend on database state, so all rounds of a question are extracted concurrently into the
   cache; then `ingest_turn` replays them in time order (reconcile and apply are sequential).
   One user per question (`lme-<id>`). A marker keyed by a hash of the models and prompts
   skips re-ingestion and invalidates itself when a prompt changes.
4. **Fairness rules.** Same answer model for all systems. `rag` and `ours` get the same context
   budget; `full` is unbudgeted (it is the cost upper bound). RAG's index build (embedding the
   history) is a write-time cost and is excluded from its read latency; our read latency
   includes the query-rewrite LLM call because that is a real cost of our design.
5. **Stratified, seeded sampling** by question type, so `(n, seed)` names one fixed set.
6. **Errors are results.** A question that errors is recorded with its error, excluded from
   accuracy, and counted in the table, never silently dropped.

## Trade-off
Using a Claude judge to grade Claude answers can favour Claude-style answers; the hand-labelled
50-answer agreement check from ADR 0008 is the mitigation and is still to do. Ingestion is
sequential per question (about 11 minutes each), so a full run is hours of wall time and
relies on running many questions at once.

**In an interview:** "I kept the benchmark's prompts and decision rule but couldn't use its
judge model, so I recorded that in every result and only compare my own variants against each
other. Every model call is cached by prompt, so an ablation costs only what actually changed."
