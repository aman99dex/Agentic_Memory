"""The read path: question -> rewrite -> hybrid search -> expand to turns -> pack (ADR 0003).

Facts are the search keys; the original turns carry the detail, so they are what we hand back.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from agentic_memory.context import tokens as _tokens
from agentic_memory.rank import Rank
from agentic_memory.store import Kind, Memory, Retriever, Store, Turn
from agentic_memory.telemetry import tracer

TOP_K = 10
ReturnMode = Literal["facts", "turns", "both"]

REWRITE_PROMPT = """Rewrite a question so it can be searched on its own, and say whether it asks
about a past moment. The current date is {now}.

- search_text: replace pronouns and references ("she", "that", "it") using the recent conversation.
  Keep every concrete word. If the question is already clear, return it unchanged.
- as_of: only when the question asks what was true at a specific past time ("where did I live in
  March?"), that moment as an ISO 8601 date. Otherwise null. "Now" and "currently" are null.
- past_sessions: true only when the question asks what happened, was done or was discussed in
  earlier sessions ("what did we do yesterday?", "where did we leave the migration?"). False
  for questions about facts, preferences or the current state ("where do I live?").

Recent conversation (may be empty):
{context}

Question: {query}"""


class Query(BaseModel):
    search_text: str = Field(description="Self-contained search text")
    as_of: datetime | None = Field(default=None, description="Past moment asked about, or null")
    past_sessions: bool = Field(
        default=False, description="Asks what happened or was done in earlier sessions"
    )


@dataclass(frozen=True)
class Recall:
    text: str  # ready to paste into a prompt
    memories: list[Memory]
    turns: list[Turn]
    query: Query


def _fact_line(m: Memory) -> str:
    if m.kind == "episode":  # a summary of what happened in one session (ADR 0021)
        return f"- [{m.id}] session on {m.valid_from.date()}: {m.content}"
    return f"- [{m.id}] {m.content} (since {m.valid_from.date()})"


def _turn_block(t: Turn) -> str:
    return f"[{t.occurred_at.date()}] user: {t.user_text}\nassistant: {t.assistant_text}"


def pack(
    memories: Sequence[Memory], turns: Sequence[Turn], budget: int, sessions_first: bool = False
) -> str:
    """Greedy: facts (compact) first, then session summaries, then source turns, each in rank
    order, until the budget. For a question about past work, the summaries go first.

    Within each list it stops at the first item that doesn't fit, so a lower-ranked item never
    jumps ahead of a higher one. Whatever budget is left still goes to the next list.
    Summaries get their own heading: they are history, never current truth (ADR 0021).
    """
    facts = ("Known facts:", [_fact_line(m) for m in memories if m.kind != "episode"])
    sessions = ("Past sessions:", [_fact_line(m) for m in memories if m.kind == "episode"])
    parts: list[str] = []
    used = 0
    for header, items in (
        *((sessions, facts) if sessions_first else (facts, sessions)),
        ("Source conversations:", [_turn_block(t) for t in turns]),
    ):
        lines: list[str] = []
        for item in items:
            cost = _tokens(item)
            if used + cost > budget:
                break
            lines.append(item)
            used += cost
        if lines:
            parts.append(header + "\n" + "\n".join(lines))
    return "\n\n".join(parts)


async def recall(
    store: Store,
    query_llm: Any,
    user_id: str,
    query: str,
    *,
    now: datetime,
    context: Sequence[str] = (),
    budget: int = 1500,
    mode: ReturnMode = "both",
    retriever: Retriever = "hybrid",
    rank: Rank | None = None,
    rewrite: bool = True,
    namespace: str | None = None,
) -> Recall:
    """`rewrite=False` skips the LLM call: the query is searched as typed, with no time filter.
    With a namespace, only user-wide memories and that namespace's are searched (ADR 0021)."""
    with tracer.start_as_current_span("recall"):
        if rewrite:
            with tracer.start_as_current_span("recall.rewrite"):
                rewriter = query_llm.with_structured_output(Query)
                q: Query = await rewriter.ainvoke(
                    REWRITE_PROMPT.format(
                        now=now.date().isoformat(),
                        context="\n".join(context) or "(none)",
                        query=query,
                    )
                )
        else:
            q = Query(search_text=query)
        if q.as_of and q.as_of.tzinfo is None:  # the LLM gives a bare date; the DB wants a zone
            q = q.model_copy(update={"as_of": q.as_of.replace(tzinfo=UTC)})

        # A summary can mention a fact that has since changed ("said they live in Bangalore"),
        # so summaries are searched only when the question is about past work, and then on
        # their own, so the facts can't crowd them out of the top results (ADR 0021).
        async def find(kinds: tuple[Kind, ...], limit: int) -> list[Memory]:
            return await store.search(
                user_id,
                q.search_text,
                limit=limit,
                as_of=q.as_of,
                retriever=retriever,
                rank=rank,
                now=now,
                namespace=namespace,
                kinds=kinds,
            )

        with tracer.start_as_current_span("recall.search"):
            memories = await find(("episode",), TOP_K) if q.past_sessions else []
            # Facts fill what the summaries leave, but always at least half: with no summaries
            # (the evaluation never writes any) a past-work question still gets all 10 facts.
            memories += await find(("fact",), max(TOP_K // 2, TOP_K - len(memories)))
        with tracer.start_as_current_span("recall.expand_and_pack"):
            turn_ids = list(dict.fromkeys(i for m in memories for i in m.source_turn_ids))
            turns = await store.get_turns(user_id, turn_ids) if mode != "facts" else []
            shown = memories if mode != "turns" else []
            text = pack(shown, turns, budget, sessions_first=q.past_sessions)
            return Recall(text, shown, turns, q)
