# 0017. LoCoMo as a second benchmark, converted into the LongMemEval shape

**Status:** Accepted, 2026-10-29. Extends ADR 0015.

## Context
The plan names LongMemEval and LoCoMo. LoCoMo (Maharana et al., 2024) is 10 long conversations
(about 590 turns each) with 1986 questions in five categories. The data file is
`locomo10.json` from the snap-research/locomo repository; its structure was checked against the
downloaded file, not memory: sessions are `session_N` lists of `{speaker, dia_id, text}` with a
`session_N_date_time` such as `1:56 pm on 8 May, 2023`, and `qa` items carry `category` 1-5 and
`evidence` as `D<session>:<turn>`.

## Decision
- **Convert, don't fork the harness** (`eval/locomo.py`): each conversation becomes questions in
  the LongMemEval shape, so the same three systems, cache, report and statistics run unchanged.
- **One history per conversation, not per question.** About 200 questions share a conversation,
  so a question carries `history_id`; ingestion runs once per history under a lock, and the RAG
  index is cached per history.
- **Categories 1-4 only** (multi-hop, temporal, open-domain, single-hop). Category 5
  (adversarial) has no gold answer in the data; Mem0's evaluation drops it too.
- **Speakers:** the two speakers become alternating `user` / `assistant` turns by position, with
  the speaker's name kept in the text ("Caroline: ..."), so no turn is dropped and questions that
  name a person can be answered.
- **No question date in the data:** questions are asked "as of" the last session's date.
- **Judge:** LoCoMo's own metrics are token F1 / BLEU, which punish correct paraphrases. We use
  the LLM judge with the same yes/no rule as LongMemEval and a prompt we wrote
  (`eval/judge.py:locomo_prompt`). It is **not** an official grader, and every report says so.

## Trade-off
- Our extraction prompt extracts facts about "the USER". In LoCoMo both speakers are people, so
  facts about the second speaker are often dropped. That handicaps `ours` on LoCoMo; we report it
  rather than tune the prompt to a benchmark, because ADR 0013's prompt is the product.
- LoCoMo numbers use a different judge prompt than LongMemEval, so they are not comparable across
  the two datasets, only across systems within one.

**In an interview:** "I made LoCoMo look like LongMemEval instead of building a second harness,
so every statistical guarantee carried over for free. I dropped the adversarial category for the
same reason Mem0 did, and I say in the report that the judge is mine, not the benchmark's."
