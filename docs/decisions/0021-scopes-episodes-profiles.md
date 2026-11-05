# 0021. Facts have a scope; sessions get summaries; scopes get profiles

**Status:** Accepted, 2026-11-04. Phase 7. Adds the two higher layers that TencentDB Agent
Memory calls L2 (scenario) and L3 (persona), and that Mastra builds with an observer and a
reflector.

## Context
Three failures in the 2026-11-04 session:
1. **No scope.** "We deploy to Cloud Run" from one repo would show up in every repo.
2. **Only facts, never what happened.** "What did we do on the first day?" found nothing:
   the extractor drops tasks like "fixed the import" by design.
3. **No overview.** A new session had to search before it knew anything about the user.

## Decision
- **Scopes.** Every fact has a `namespace`: NULL means true for this user everywhere ("always
  use type hints"), a value means one project, app or agent ("deploys to Cloud Run"). The
  extractor labels each fact `user` or `project`; without a namespace everything is user-wide.
  A search inside a namespace sees the user-wide facts plus that namespace's; reconcile compares
  a new fact only with facts it could replace in that reach. Claude Code's namespace is the
  git root of the working directory (`config.project_namespace`).
- **Episodes.** After processing, the worker folds each session's new turns into a rolling
  summary of the *work* (what was done, decided, left open), one row per session, rewritten in
  place. The prompt forbids restating facts about the user, and a session with no work gets no
  summary ("NONE").
- **Profiles.** When a scope's facts change, the worker rewrites a short profile of the user
  (namespace NULL) or a brief of the project from its current facts. One row per scope,
  rewritten in place. `forget` deletes all of a user's profiles, so nothing forgotten survives
  inside a summary; the next change rebuilds them.
- **Kinds.** `memories.kind` is `fact`, `episode` or `profile`. Summaries are derived data with
  no versions: the turns and facts they came from are the record.
- **Reading.** `context` (no LLM, no embedding) returns the profile, the project brief and the
  three latest sessions; until a profile exists, the most-used facts stand in. `recall`
  searches facts only, unless the rewritten query says it is about past sessions
  (`past_sessions`); then summaries are searched on their own, so facts can't crowd them out,
  and they are packed first under "Past sessions".

## Why summaries stay out of normal search
The first version searched them with the facts. A test caught "session on 2026-01-05: the user
said they live in Bangalore" ranked first for "where do I live?", above the current fact. A
summary is a record of the past, so it must not compete with current truth.

## Trade-off
- More LLM calls: one summary update per session per drain, one profile rewrite per changed
  scope. With Haiku that is a fraction of a cent per turn.
- A summary or profile can be wrong in ways a single fact can't, because the model writes
  prose. The prompts allow only the given facts, and the facts remain searchable directly.
- The namespace for Claude Code is a path, so moving a repo starts a new project memory.

**In an interview:** "Facts are the source of truth; episodes and profiles are summaries built
from them in the background. They're kept out of fact search on purpose, because a summary of
last month will happily repeat something that's no longer true."
