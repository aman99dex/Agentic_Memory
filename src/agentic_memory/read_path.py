"""The read path: question -> rewrite -> hybrid search -> expand to turns -> pack (ADR 0003).

Facts are the search keys; the original turns carry the detail, so they are what we hand back.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from agentic_memory.store import Memory, MemoryStore, Turn

TOP_K = 10
ReturnMode = Literal["facts", "turns", "both"]

REWRITE_PROMPT = """Rewrite a question so it can be searched on its own, and say whether it asks
about a past moment. The current date is {now}.

- search_text: replace pronouns and references ("she", "that", "it") using the recent conversation.
  Keep every concrete word. If the question is already clear, return it unchanged.
- as_of: only when the question asks what was true at a specific past time ("where did I live in
  March?"), that moment as an ISO 8601 date. Otherwise null. "Now" and "currently" are null.

Recent conversation (may be empty):
{context}

Question: {query}"""


class Query(BaseModel):
    search_text: str = Field(description="Self-contained search text")
    as_of: datetime | None = Field(default=None, description="Past moment asked about, or null")


@dataclass(frozen=True)
class Recall:
    text: str  # ready to paste into a prompt
    memories: list[Memory]
    turns: list[Turn]
    query: Query


def _tokens(text: str) -> int:
    # ponytail: 4 characters ~ 1 token. Close enough for English; swap in a real tokenizer if
    # budgets must be exact.
    return len(text) // 4 + 1


def _fact_line(m: Memory) -> str:
    return f"- [{m.id}] {m.content} (since {m.valid_from.date()})"


def _turn_block(t: Turn) -> str:
    return f"[{t.occurred_at.date()}] user: {t.user_text}\nassistant: {t.assistant_text}"


def pack(memories: Sequence[Memory], turns: Sequence[Turn], budget: int) -> str:
    """Greedy: facts (compact) first, then source turns, each in rank order, until the budget.

    Within each list it stops at the first item that doesn't fit, so a lower-ranked item never
    jumps ahead of a higher one. Whatever budget is left still goes to the next list.
    """
    parts: list[str] = []
    used = 0
    for header, items in (
        ("Known facts:", [_fact_line(m) for m in memories]),
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
    store: MemoryStore,
    query_llm: Any,
    user_id: str,
    query: str,
    *,
    now: datetime,
    context: Sequence[str] = (),
    budget: int = 1500,
    mode: ReturnMode = "both",
) -> Recall:
    rewriter = query_llm.with_structured_output(Query)
    q: Query = await rewriter.ainvoke(
        REWRITE_PROMPT.format(
            now=now.date().isoformat(), context="\n".join(context) or "(none)", query=query
        )
    )
    if q.as_of and q.as_of.tzinfo is None:  # the LLM gives a bare date; the database wants a zone
        q = q.model_copy(update={"as_of": q.as_of.replace(tzinfo=UTC)})
    memories = await store.search(user_id, q.search_text, limit=TOP_K, as_of=q.as_of)
    turn_ids = list(dict.fromkeys(i for m in memories for i in m.source_turn_ids))
    turns = await store.get_turns(user_id, turn_ids) if mode != "facts" else []
    shown_memories = memories if mode != "turns" else []
    return Recall(pack(shown_memories, turns, budget), shown_memories, turns, q)
