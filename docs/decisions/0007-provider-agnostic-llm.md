# 0007. Provider-agnostic LLM layer, cheap model for extraction

**Status:** Accepted, 2026-10-16

## Context
LLM calls happen in three places, and their cost profiles differ:

| Role | Volume | Needs |
|---|---|---|
| Extract + reconcile (write path) | Every ingested turn. LongMemEval_s is about 57M haystack tokens. | Cheap, reliable structured output |
| Query rewrite / time-range extraction (read path) | Every query | Low latency |
| Answer + judge (eval only) | 500 questions per run | Quality; the judge must stay fixed |

## Decision
- All models are created through `langchain.chat_models.init_chat_model("<provider>:<model>")`,
  with one setting per role: `EXTRACT_MODEL`, `QUERY_MODEL`, `ANSWER_MODEL`. Switching
  provider is an env-var change.
- Default suggestion: a Haiku-class model for extract and query, a Sonnet-class model for
  answers. At Haiku 5.5's $0.10/M input, extraction over all of LongMemEval_s costs about
  $10–20 instead of hundreds.
- **The judge is pinned** to the benchmark's official judge (`gpt-4o-2024-08-06`, the
  official prompts), so our numbers are comparable to published ones. Changing the judge
  invalidates comparisons, so it is versioned in every results file.
- Extraction results are **cached by a hash of the session content**. LongMemEval reuses
  filler sessions across questions, so the same session is never paid for twice.

**In an interview:** "Each LLM role is a config value, so I can put a cheap model on the
hot path. I pinned the judge to the benchmark's own, because otherwise scores aren't
comparable."
