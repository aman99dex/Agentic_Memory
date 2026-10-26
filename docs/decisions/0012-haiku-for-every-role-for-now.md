# 0012. Haiku for every LLM role during development

**Status:** Accepted, 2026-10-18. Changes the default model in ADR 0007; the
provider-agnostic design in 0007 still stands.

## Context
ADR 0007 suggested a Haiku-class model for extract and query, and a Sonnet-class model
for answers. Aman wants the lowest token spend while building, on a personal Anthropic
key.

## Options
| Option | Cost | Quality |
|---|---|---|
| Haiku for extract/query, Sonnet for answers (0007) | Higher on answers | Better answers |
| **Haiku for every role** | Lowest Anthropic cost | Weaker answers and harder reasoning |
| Local model (Ollama) | No API cost | Unreliable structured output, slow |

## Decision
`EXTRACT_MODEL`, `QUERY_MODEL` and `ANSWER_MODEL` all default to
`anthropic:claude-haiku-5-5`. Each one is still an env var, so this costs no code to undo.
The benchmark judge is not covered by this ADR: it stays pinned (0007), and we revisit
it in Phase 5.

## Trade-off
Answer quality in early demos understates what the system can do. Phase 5 will report
which answering model produced each number, and we can rerun answers with a stronger model
without re-extracting, because extraction is cached.

**In an interview:** "Every model is a config value. I developed on the cheapest one and
kept the extraction cache, so upgrading only the answering model is a cheap rerun."
