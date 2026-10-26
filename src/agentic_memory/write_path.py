"""The write path: turn -> extract facts -> reconcile with what we know -> act (ADR 0003, 0004).

The LLM only picks a label; code follows the edges and runs the SQL.
"""

import os
from datetime import datetime
from typing import Any, Literal, TypedDict

from langchain.chat_models import init_chat_model
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from agentic_memory.store import Memory, MemoryStore

CANDIDATES = 5  # how many similar stored facts the reconcile step gets to see

EXTRACT_PROMPT = """You extract durable facts about the USER from one round of conversation.
Today's date for this conversation is {when}; turn relative dates ("last week") into absolute ones.

Rules:
- One short, self-contained sentence per fact, e.g. "The user lives in Pune."
- Only things worth remembering later: identity, preferences, plans, relationships, events,
  decisions, changes. Skip chit-chat, thanks, questions and anything the assistant said that
  the user did not confirm.
- A change is ONE fact stating the new truth ("The user eats chicken."), never also a separate
  negation of the old one ("The user is no longer vegetarian.").
- Say each thing once. Do not restate a fact with a date or detail added as a second fact.
- If there is nothing worth remembering, return an empty list.

USER: {user_text}
ASSISTANT: {assistant_text}"""

RECONCILE_PROMPT = """A memory system holds these CURRENT facts about a user:
{candidates}

A new fact was just extracted: "{fact}"

Choose exactly one action:
- ADD: the fact is new information, unrelated to every stored fact.
- UPDATE: it states a new truth that replaces a stored fact (set memory_id to that fact), e.g.
  stored "is vegetarian", new "eats chicken". Also UPDATE when a stored plan has now happened:
  stored "is moving to Pune", new "lives in Pune".
- INVALIDATE: it only says a stored fact ended, with no new truth to store (set memory_id), e.g.
  stored "works at Neoflo", new "quit their job".
- NOOP: it is already covered by a stored fact, or says nothing new."""


Action = Literal["ADD", "UPDATE", "INVALIDATE", "NOOP"]


class Facts(BaseModel):
    facts: list[str] = Field(description="Self-contained sentences about the user")


class Decision(BaseModel):
    action: Action
    memory_id: int | None = Field(
        default=None, description="The stored fact to replace or close; null for ADD and NOOP"
    )


class Step(TypedDict):
    fact: str
    action: Action
    memory_id: int | None


class State(TypedDict):
    user_id: str
    turn_id: int
    user_text: str
    assistant_text: str
    occurred_at: datetime
    facts: list[str]
    steps: list[Step]


def make_llm(role: str = "EXTRACT_MODEL") -> Any:
    """The model for a role is an env var (ADR 0007), e.g. anthropic:claude-haiku-5-5."""
    return init_chat_model(os.environ[role])  # no temperature: Haiku 5.5 rejects the parameter


def _show(memories: list[Memory]) -> str:
    return "\n".join(f"[{m.id}] {m.content}" for m in memories) or "(none)"


def _check(decision: Decision, shown: set[int]) -> tuple[Action, int | None]:
    """Never trust a label blindly: a target must be one of the facts the LLM was shown."""
    action, target = decision.action, decision.memory_id
    if action in ("UPDATE", "INVALIDATE") and target not in shown:
        # A fact that replaces nothing real is still new information; a close of nothing is a no-op.
        return ("ADD", None) if action == "UPDATE" else ("NOOP", None)
    return action, (target if action in ("UPDATE", "INVALIDATE") else None)


def build_graph(store: MemoryStore, extract_llm: Any, reconcile_llm: Any) -> Any:
    extractor = extract_llm.with_structured_output(Facts)
    judge = reconcile_llm.with_structured_output(Decision)

    async def extract(state: State) -> dict[str, Any]:
        out: Facts = await extractor.ainvoke(
            EXTRACT_PROMPT.format(
                when=state["occurred_at"].date().isoformat(),
                user_text=state["user_text"],
                assistant_text=state["assistant_text"],
            )
        )
        return {"facts": [f.strip() for f in out.facts if f.strip()]}

    async def reconcile(state: State) -> dict[str, Any]:
        steps: list[Step] = []
        for fact in state["facts"]:
            found = await store.search(state["user_id"], fact, limit=CANDIDATES)
            decision: Decision = await judge.ainvoke(
                RECONCILE_PROMPT.format(candidates=_show(found), fact=fact)
            )
            action, target = _check(decision, {m.id for m in found})
            steps.append({"fact": fact, "action": action, "memory_id": target})
        return {"steps": steps}

    async def apply(state: State) -> dict[str, Any]:
        uid, at, src = state["user_id"], state["occurred_at"], [state["turn_id"]]
        for s in state["steps"]:
            # ponytail: turns must arrive in time order; a fact older than the one it replaces
            # trips the valid_to >= valid_from CHECK. Sort by occurred_at on bulk import.
            if s["action"] == "ADD":
                await store.add(uid, s["fact"], src, at)
            elif s["action"] == "UPDATE":
                assert s["memory_id"] is not None
                await store.supersede(uid, s["memory_id"], s["fact"], src, at)
            elif s["action"] == "INVALIDATE":
                assert s["memory_id"] is not None
                await store.invalidate(uid, s["memory_id"], at)
        return {}

    def has_actions(state: State) -> str:
        return "apply" if any(s["action"] != "NOOP" for s in state["steps"]) else END

    def has_facts(state: State) -> str:
        return "reconcile" if state["facts"] else END

    g = StateGraph(State)
    g.add_node("extract", extract)
    g.add_node("reconcile", reconcile)
    g.add_node("apply", apply)
    g.add_edge(START, "extract")
    g.add_conditional_edges("extract", has_facts, ["reconcile", END])
    g.add_conditional_edges("reconcile", has_actions, ["apply", END])
    g.add_edge("apply", END)
    return g.compile()


async def ingest_turn(
    store: MemoryStore,
    graph: Any,
    user_id: str,
    session_id: str,
    user_text: str,
    assistant_text: str,
    occurred_at: datetime,
) -> list[Step]:
    """Store the turn, then turn it into facts. Safe to call twice with the same round."""
    turn_id, is_new = await store.add_turn(
        user_id, session_id, user_text, assistant_text, occurred_at
    )
    if not is_new and await store.turn_has_memories(user_id, turn_id):
        return []  # already processed
    # A repeat with no facts yet is retried: an earlier crash must not lose the turn for good.
    # Retrying is safe because reconcile answers NOOP for facts that are already stored.
    final = await graph.ainvoke(
        {
            "user_id": user_id,
            "turn_id": turn_id,
            "user_text": user_text,
            "assistant_text": assistant_text,
            "occurred_at": occurred_at,
            "facts": [],
            "steps": [],
        }
    )
    steps: list[Step] = final["steps"]
    return steps
