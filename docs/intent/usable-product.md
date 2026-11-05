# Intent: a memory people actually install and keep using

Confirmed by Aman on 2026-11-04, before the evaluation is run.

- **Outcome:** Agentic Memory is something people install and keep using, not a one-off demo.
- **Users:** developers adding it to their own coding agent (Claude Code, Cursor, any MCP
  client), and teams putting it inside their product.
- **Why now:** benchmark numbers on a system nobody can use don't prove much.
- **Success:** on a fresh machine, two commands get Claude Code remembering across sessions
  without being told to. A sample product integrates it in about 20 lines through the SDK.
- **Constraints:** every decision stays defensible in an interview, with an ADR. Postgres stays
  for products.
- **Out of scope:** team memory, a wiki, a code graph, and a hosted service run for others.

## What a real session showed (2026-11-04)

A developer's week of Claude Code turns, run through the real service and real Haiku:

| Problem | Why it blocks real use |
|---|---|
| Five setup steps, including Docker and a running Postgres | Claude Code's built-in memory needs zero |
| Nothing saved unless the agent calls `remember` | Agents skip the call |
| Saving blocks for 1.4 to 6.2 seconds | A chat product can't wait that long per message |
| No per-project scope | A fact about one repo shows up in every repo |
| Only facts about the user, never what happened | "What did we do on the first day?" got nothing |
| One shared API key, and a client that installs the whole server | Products can't adopt it cleanly |

## Order of work

1. Local SQLite mode with a one-command install.
2. Background saving, plus scoping by project and by session.
3. Claude Code hooks for automatic capture, plus episodic memory.
4. A background step that builds a user profile and project summaries.
5. The product side: per-tenant API keys, a slim client package, bulk import.
6. The evaluation, run on the finished system.
