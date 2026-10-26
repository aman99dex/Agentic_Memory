# 0013. The reconcile LLM returns a label and a target, never text; code guards the target

**Status:** Accepted, 2026-10-26

## Context
The write path (Phase 2) lets an LLM decide how a new fact relates to stored ones. An LLM
that can emit anything can corrupt memory with one bad answer: invent ids, rewrite facts,
or name another user's row.

## Options
| Option | Control | Risk |
|---|---|---|
| LLM returns the final fact text and SQL-ready ops | Highest flexibility | Hallucinated ids, paraphrase drift, hard to test |
| **LLM returns `action` (4 labels) + `memory_id`; fact text comes from the extractor** | Output space is tiny and typed | Judge can't improve wording |
| Rules only, no LLM reconcile | Fully deterministic | Can't tell "vegetarian" from "eats chicken" |

## Decision
`Decision = {action: ADD|UPDATE|INVALIDATE|NOOP, memory_id: int | None}`, validated by
Pydantic. Code checks the target against the candidate ids the LLM was shown: a bad target
turns `UPDATE` into `ADD` and `INVALIDATE` into `NOOP`. Graph routing is plain Python on the
label. `store.supersede` runs insert + close in one transaction. A repeated turn that
produced no facts yet is re-run, not skipped, so a crash never loses a turn; reconcile's
`NOOP` makes the re-run safe.

## Trade-off
Wording is only as good as the extractor's. Re-running a turn with genuinely no facts costs
one LLM call; a `processed_at` column removes that if it ever matters. Turns must be ingested
in time order (CHECK on `valid_to >= valid_from`).

**In an interview:** "The LLM classifies and points; code verifies the pointer and executes.
A wrong answer is one of four safe actions, never arbitrary SQL."
