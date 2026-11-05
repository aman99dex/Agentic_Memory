"""The background worker (ADR 0020) and the summaries it keeps up to date (ADR 0021).

A save only stores the turn. The worker then, per user and under that user's lease:
1. turns each pending turn into facts, oldest first (extract -> reconcile -> apply);
2. rewrites the summary of every session that got new turns (episodic memory);
3. rewrites the profile of every scope whose facts changed (the user, the project).

Any number of workers can run (API replicas, the MCP server, a Claude Code hook): the lease
lets only one of them work on a given user at a time.
"""

import asyncio
import logging
import os
import socket
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from agentic_memory.store import Memory, Store, Turn
from agentic_memory.write_path import Step, process_turn

log = logging.getLogger(__name__)

LEASE_SECONDS = 300  # renewed before every turn; a crashed worker's lease frees itself
BATCH = 20
TURN_CHARS = 2000  # per side of a turn, when it is shown to the summariser
SESSION_TURNS = 30  # the most new turns one summary update reads

EPISODE_PROMPT = """You keep a short running summary of one session between a user and an AI
assistant, so the next session can pick up where this one stopped. Summarise the WORK: what
was done, built, changed, decided, found broken, or left unfinished. Past tense, 1 to 5 short
sentences, concrete names (files, tools, people, numbers), no filler. Merge the new turns into
the summary so far; drop details that stopped mattering.
Do not restate facts about the user or their preferences ("lives in Pune", "likes tea"): those
are stored separately and may change. If the session has no work worth summarising (only
facts, greetings or small talk), answer exactly NONE.

Summary so far:
{summary}

New turns:
{turns}"""

PROFILE_RULES = """Plain bullet points ("- ..."), most useful first, at most 120 words. Use only
these facts and never invent. Leave out any topic the facts don't cover: no "not specified",
no headings, no advice about what to assume."""

PROFILE_PROMPTS = {
    "user": """Write a short profile of this user for an AI assistant to read before it helps
them: who they are, how they like to work, standing instructions. """
    + PROFILE_RULES
    + "\n\nFacts:\n{facts}",
    "project": """Write a short brief of this project for an AI assistant about to work in it:
what it is, its stack and hosting, conventions to follow, decisions in force. """
    + PROFILE_RULES
    + "\n\nFacts:\n{facts}",
}


def text_of(message: Any) -> str:
    """The plain text of a chat model reply (content may be a string or a list of blocks)."""
    content = message.content if hasattr(message, "content") else message
    if isinstance(content, str):
        return content.strip()
    return "".join(b.get("text", "") for b in content if isinstance(b, dict)).strip()


def _clip(text: str) -> str:
    return text if len(text) <= TURN_CHARS else text[:TURN_CHARS] + " [...]"


def _render(turns: Sequence[Turn]) -> str:
    return "\n".join(
        f"[{t.occurred_at:%Y-%m-%d %H:%M}] user: {_clip(t.user_text)}\n"
        f"assistant: {_clip(t.assistant_text)}"
        for t in turns
    )


class Worker:
    def __init__(self, store: Store, graph: Any, llm: Any, holder: str | None = None) -> None:
        self.store, self.graph, self.llm = store, graph, llm
        self.holder = holder or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
        self.wake = asyncio.Event()  # set by a save, so the loop doesn't wait out its poll

    async def drain_user(self, user_id: str) -> dict[int, list[Step]]:
        """Process every pending turn of one user. Returns the steps of each turn processed;
        an empty dict when another worker holds this user's lease."""
        if not await self.store.claim(user_id, self.holder, datetime.now(UTC), LEASE_SECONDS):
            return {}
        done: dict[int, list[Step]] = {}
        sessions: dict[str, str | None] = {}  # session -> its namespace
        changed: set[str | None] = set()  # scopes whose facts changed
        try:
            while batch := await self.store.pending_turns(user_id, BATCH):
                progress = False
                for turn in batch:
                    await self.store.claim(user_id, self.holder, datetime.now(UTC), LEASE_SECONDS)
                    try:
                        steps = await process_turn(self.store, self.graph, user_id, turn)
                    except Exception as e:  # any failure: record it, retry on a later drain
                        log.warning("turn %s of %s failed: %r", turn.id, user_id, e)
                        await self.store.finish_turn(
                            turn.id, datetime.now(UTC), f"{type(e).__name__}: {e}"
                        )
                        continue
                    progress = True
                    done[turn.id] = steps
                    sessions[turn.session_id] = turn.namespace
                    changed.update(s["namespace"] for s in steps if s["action"] != "NOOP")
                if not progress:
                    break  # every turn in this batch failed: leave them for the next drain
            for session_id, namespace in sessions.items():
                await self._safely(self.summarize_session(user_id, session_id, namespace))
            for namespace in changed:
                await self._safely(self.reflect(user_id, namespace))
        finally:
            await self.store.release(user_id, self.holder)
        return done

    @staticmethod
    async def _safely(job: Any) -> None:
        try:
            await job
        except Exception as e:  # a summary failing must never lose the facts already saved
            log.warning("summary update failed: %r", e)

    async def drain(self) -> int:
        """Process every user's pending turns. Returns how many turns were processed."""
        total = 0
        for user_id in await self.store.pending_users():
            total += len(await self.drain_user(user_id))
        return total

    async def run_forever(self, poll_seconds: float = 2.0) -> None:
        while True:
            try:
                await self.drain()
            except Exception as e:  # the loop must outlive a database blip
                log.warning("worker drain failed: %r", e)
            try:
                await asyncio.wait_for(self.wake.wait(), poll_seconds)
            except TimeoutError:
                pass
            self.wake.clear()

    async def summarize_session(self, user_id: str, session_id: str, namespace: str | None) -> None:
        """Fold the session's new turns into its rolling summary (an episode)."""
        existing = await self.store.derived(user_id, "episode", session_id=session_id)
        seen = existing.source_turn_ids if existing else []
        turns = await self.store.session_turns(user_id, session_id, max(seen, default=0))
        if not turns:
            return
        prompt = EPISODE_PROMPT.format(
            summary=existing.content if existing else "(none yet)",
            turns=_render(turns[-SESSION_TURNS:]),
        )
        summary = text_of(await self.llm.ainvoke(prompt))
        if summary.upper().rstrip(".") == "NONE":
            if existing is None:
                return  # nothing worth a summary yet; these turns are read again next time
            summary = existing.content  # keep it, but mark the new turns as read
        await self.store.put_derived(
            user_id,
            "episode",
            summary,
            [*seen, *(t.id for t in turns)],
            existing.valid_from if existing else turns[0].occurred_at,
            namespace=namespace,
            session_id=session_id,
        )

    async def reflect(self, user_id: str, namespace: str | None) -> None:
        """Rewrite the profile of one scope from its current facts (None = the user)."""
        facts: list[Memory] = await self.store.current_facts(user_id, namespace)
        if not facts:
            return
        prompt = PROFILE_PROMPTS["user" if namespace is None else "project"].format(
            facts="\n".join(f"- {m.content}" for m in facts)
        )
        profile = text_of(await self.llm.ainvoke(prompt))
        await self.store.put_derived(
            user_id, "profile", profile, [], datetime.now(UTC), namespace=namespace
        )
