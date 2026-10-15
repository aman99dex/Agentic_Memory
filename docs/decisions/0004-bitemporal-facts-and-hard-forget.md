# 0004. Keep history with `valid_to`, plus a real hard delete

**Status:** Accepted, 2026-10-16

## Context
Facts change: "I live in Bangalore" is replaced by "I moved to Pune". Mem0 **deletes**
the old fact, so "where did I live before?" becomes unanswerable. Zep/Graphiti keeps
**bi-temporal** edges:
- *valid time*: when the fact was true in the world;
- *transaction time*: when we learned it.

But a "never delete" design conflicts with GDPR Art. 17 (right to erasure).

## Decision
- **Update path (normal):** never delete. Set `valid_to` on the old row and point its
  `superseded_by` at the new one. Every memory row also carries `created_at`, which is
  the transaction time. Queries return only current facts (`valid_to IS NULL`) unless
  `as_of=<timestamp>` is passed.
- **Forget path (privacy):** `DELETE /memories?subject=...` hard-deletes the matching
  memories, their source turns and their embeddings. It writes a deletion record that
  holds counts only, no content. Backups expire on a fixed retention window, which is
  documented.

## Honest expectation
Full-context models already do fairly well on LongMemEval's knowledge-update category,
and its judge accepts an answer that mentions the old value too, as long as the new one
is present. Invalidation should help most with **retrieval precision** (stale facts no
longer fill the budget) and with **temporal** questions. Phase 5's ablation turns
history-keeping off to measure this rather than assume it.

**In an interview:** "Updates are versioned, not destructive, so I can answer 'what did
I believe in March'. Erasure is a separate hard-delete path, because 'never delete' and
GDPR can't both hold."
