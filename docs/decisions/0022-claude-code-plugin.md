# 0022. Claude Code: a plugin with three hooks, plus the MCP server

**Status:** Accepted, 2026-11-04. Phase 7. Builds on ADR 0014 (MCP server).

## Context
An MCP server alone depends on the agent deciding to call `remember`, and agents skip it.
TencentDB Agent Memory captures everything through a proxy; Mastra sits inside the agent loop.
Claude Code offers hooks: commands it runs at fixed moments, with JSON on stdin.

Facts from the Claude Code docs this rests on: SessionStart hooks can add context (plain stdout
works); UserPromptSubmit gets `prompt`; Stop gets `last_assistant_message`; plugins bundle
`hooks/hooks.json` and `.mcp.json`; `${CLAUDE_PROJECT_DIR}` expands in plugin MCP args. The
transcript file's format is internal and changes between versions, so we never parse it.

## Decision
- **SessionStart** (`agentic-memory hook session-start`, 20 s timeout): prints the `context`
  briefing for the user and this project. No LLM, no embedding; about 0.6 s warm. If anything is
  still queued, it also starts a worker.
- **UserPromptSubmit** (`hook prompt`, 10 s): saves the prompt and its time to
  `~/.agentic-memory/hooks/<session>.json`. About 0.2 s.
- **Stop** (`hook stop`, 20 s): pairs that prompt with `last_assistant_message`, stores the turn
  (no LLM), then starts a **detached** worker process and returns.
- **MCP server** (`agentic-memory mcp --project ${CLAUDE_PROJECT_DIR}`): `context`, `recall`,
  `remember`, `forget`, with instructions telling clients without hooks when to call them. It
  runs a worker too.
- **Every hook exits 0.** A memory problem must never break a session; errors go to
  `~/.agentic-memory/agentic-memory.log`.
- **Install:** `uv tool install "agentic-memory[server] @ git+https://github.com/aman99dex/Agentic_Memory"`,
  then `agentic-memory setup claude-code`, which writes `config.env` and runs
  `claude plugin marketplace add` + `claude plugin install`.

## Why the worker is a detached process, not an async hook
The first version made Stop an `async: true` hook that processed the turn itself. A real
`claude -p` run stored nothing: Claude Code ended the hook process when the session ended, before
it had even stored the turn. A user who quits right after the last answer would lose that turn
the same way. Now the hook only stores (under a second), and the slow LLM work runs in a process
started with `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB` on
Windows and `start_new_session` elsewhere, which outlives Claude Code. Leases (ADR 0020) make
duplicate workers harmless.

## Trade-off
- The Stop hook adds about half a second at the end of each answer.
- The last turn of a session reaches the next session's briefing only if the worker finished
  in between (seconds, normally). SessionStart restarts it if it didn't.
- Hooks need the `agentic-memory` command on PATH (`uv tool install` puts it there).
- The model needs an API key (`config.env`). A Claude subscription does not provide one;
  `EXTRACT_MODEL` can point at any LangChain provider, including a local Ollama model.

**In an interview:** "Capture can't depend on the model remembering to call a tool, so hooks do
it: store on Stop in under a second, process in a detached worker, brief the next session on
start. The first version lost turns because Claude Code kills hook processes on exit; I found
that in a real end-to-end run, not in unit tests."
