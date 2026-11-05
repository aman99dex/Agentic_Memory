"""The write path: turn -> extract facts -> reconcile with what we know -> act (ADR 0003, 0004).

The LLM only picks a label; code follows the edges and runs the SQL.
"""

import os
from datetime import UTC, datetime
from typing import Any, Literal, TypedDict

from langchain.chat_models import init_chat_model
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from agentic_memory.store import Memory, Store, Turn
from agentic_memory.telemetry import UsageCallback, traced, tracer

CANDIDATES = 5  # how many similar stored facts the reconcile step gets to see

EXTRACT_PROMPT = """You extract durable facts about the USER, and about the project they are
working in, from one round of conversation.
Today's date for this conversation is {when}; turn relative dates ("last week") into absolute ones.

Rules:
- One short, self-contained sentence per fact, e.g. "The user lives in Pune."
- Only things worth remembering later: identity, preferences, plans, relationships, events,
  decisions, conventions, changes. Skip chit-chat, thanks, questions, one-off tasks ("rename
  foo") and anything the assistant said that the user did not confirm.
- A change is ONE fact stating the new truth, never also a separate negation of the old one:
  "We're moving off Black, use ruff" -> only "The project formats code with ruff."
  "I'm not vegetarian anymore" -> only "The user eats meat."
- Say each thing once. Do not restate a fact with a date or detail added as a second fact.
- scope: "user" when it is about the person and true wherever they are (identity, habits,
  preferences, how they like to work). "project" when it is only about the current project,
  product or codebase (its stack, hosting, conventions, decisions).
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


class Fact(BaseModel):
    text: str = Field(description="One self-contained sentence")
    scope: Literal["user", "project"] = Field(
        default="user", description="user: true everywhere; project: only this project"
    )


class Facts(BaseModel):
    facts: list[Fact] = Field(description="Durable facts about the user and their project")


class Decision(BaseModel):
    action: Action
    memory_id: int | None = Field(
        default=None, description="The stored fact to replace or close; null for ADD and NOOP"
    )


class Step(TypedDict):
    fact: str
    action: Action
    memory_id: int | None
    namespace: str | None  # None: true for this user everywhere (ADR 0021)


class State(TypedDict):
    user_id: str
    turn_id: int
    namespace: str | None
    user_text: str
    assistant_text: str
    occurred_at: datetime
    facts: list[Fact]
    steps: list[Step]


def make_llm(role: str = "EXTRACT_MODEL") -> Any:
    """The model for a role is an env var (ADR 0007), e.g. anthropic:claude-haiku-5-5."""
    name = os.environ[role]
    # no temperature: Haiku 5.5 rejects the parameter
    return init_chat_model(name, callbacks=[UsageCallback(role, name)])


def extract_prompt(occurred_at: datetime, user_text: str, assistant_text: str) -> str:
    return EXTRACT_PROMPT.format(
        when=occurred_at.date().isoformat(), user_text=user_text, assistant_text=assistant_text
    )


def _show(memories: list[Memory]) -> str:
    return "\n".join(f"[{m.id}] {m.content}" for m in memories) or "(none)"


def _check(decision: Decision, shown: set[int]) -> tuple[Action, int | None]:
    """Never trust a label blindly: a target must be one of the facts the LLM was shown."""
    action, target = decision.action, decision.memory_id
    if action in ("UPDATE", "INVALIDATE") and target not in shown:
        # A fact that replaces nothing real is still new information; a close of nothing is a no-op.
        return ("ADD", None) if action == "UPDATE" else ("NOOP", None)
    return action, (target if action in ("UPDATE", "INVALIDATE") else None)


def build_graph(
    store: Store, extract_llm: Any, reconcile_llm: Any, keep_history: bool = True
) -> Any:
    """`keep_history=False` deletes a replaced fact instead of closing it: the ablation that
    measures what bi-temporal history is worth (ADR 0015). Production always keeps it."""
    extractor = extract_llm.with_structured_output(Facts)
    judge = reconcile_llm.with_structured_output(Decision)

    async def extract(state: State) -> dict[str, Any]:
        out: Facts = await extractor.ainvoke(
            extract_prompt(state["occurred_at"], state["user_text"], state["assistant_text"])
        )
        return {"facts": [f for f in out.facts if f.text.strip()]}

    async def reconcile(state: State) -> dict[str, Any]:
        steps: list[Step] = []
        for fact in state["facts"]:
            # Project facts only exist inside a namespace; without one, everything is user-wide.
            ns = state["namespace"] if fact.scope == "project" else None
            # Candidates: facts (never summaries) this one could replace, in the same reach.
            found = await store.search(
                state["user_id"],
                fact.text,
                limit=CANDIDATES,
                namespace=state["namespace"],
                kinds=("fact",),
            )
            decision: Decision = await judge.ainvoke(
                RECONCILE_PROMPT.format(candidates=_show(found), fact=fact.text)
            )
            action, target = _check(decision, {m.id for m in found})
            steps.append(
                {"fact": fact.text.strip(), "action": action, "memory_id": target, "namespace": ns}
            )
        return {"steps": steps}

    async def apply(state: State) -> dict[str, Any]:
        uid, at, src = state["user_id"], state["occurred_at"], [state["turn_id"]]
        for s in state["steps"]:
            # Turns are processed oldest first (ADR 0020); a late, older turn still can't break
            # the valid_to >= valid_from check, because closing clamps to the fact's start.
            ns = s["namespace"]
            if s["action"] == "ADD":
                await store.add(uid, s["fact"], src, at, ns)
            elif s["action"] == "UPDATE":
                assert s["memory_id"] is not None
                try:
                    if keep_history:
                        await store.supersede(uid, s["memory_id"], s["fact"], src, at, ns)
                    else:
                        await store.drop(uid, s["memory_id"])
                        await store.add(uid, s["fact"], src, at, ns)
                except LookupError:
                    # Another fact in this same turn already replaced the target. The fact is
                    # still new information, so keep it (same rule as an unknown id in _check).
                    await store.add(uid, s["fact"], src, at, ns)
            elif s["action"] == "INVALIDATE":
                assert s["memory_id"] is not None
                if keep_history:
                    await store.invalidate(uid, s["memory_id"], at)
                else:
                    await store.drop(uid, s["memory_id"])
        return {}

    def has_actions(state: State) -> str:
        return "apply" if any(s["action"] != "NOOP" for s in state["steps"]) else END

    def has_facts(state: State) -> str:
        return "reconcile" if state["facts"] else END

    g = StateGraph(State)
    g.add_node("extract", traced("extract", extract))
    g.add_node("reconcile", traced("reconcile", reconcile))
    g.add_node("apply", traced("apply", apply))
    g.add_edge(START, "extract")
    g.add_conditional_edges("extract", has_facts, ["reconcile", END])
    g.add_conditional_edges("reconcile", has_actions, ["apply", END])
    g.add_edge("apply", END)
    return g.compile()


async def process_turn(store: Store, graph: Any, user_id: str, turn: Turn) -> list[Step]:
    """Turn one stored round into facts and mark it done (the worker's unit of work, ADR 0020).

    Rerunning a turn that crashed half way is safe: reconcile answers NOOP for facts already
    stored. A failure is left for the caller to record, so the turn is retried later.
    """
    with tracer.start_as_current_span("process_turn") as span:
        final = await graph.ainvoke(
            {
                "user_id": user_id,
                "turn_id": turn.id,
                "namespace": turn.namespace,
                "user_text": turn.user_text,
                "assistant_text": turn.assistant_text,
                "occurred_at": turn.occurred_at,
                "facts": [],
                "steps": [],
            }
        )
        steps: list[Step] = final["steps"]
        span.set_attribute("facts.found", len(steps))
        await store.finish_turn(turn.id, datetime.now(UTC))
        return steps


async def ingest_turn(
    store: Store,
    graph: Any,
    user_id: str,
    session_id: str,
    user_text: str,
    assistant_text: str,
    occurred_at: datetime,
    namespace: str | None = None,
) -> list[Step]:
    """Store the turn and process it now, in this call. Safe to call twice with the same round.

    The evaluation and `wait=True` use this; everything else queues the turn (ADR 0020).
    """
    with tracer.start_as_current_span("ingest") as span:
        turn_id, is_new = await store.add_turn(
            user_id, session_id, user_text, assistant_text, occurred_at, namespace
        )
        span.set_attribute("turn.is_new", is_new)
        if not is_new and await store.turn_processed(user_id, turn_id):
            return []  # already processed
        turn = Turn(turn_id, session_id, user_text, assistant_text, occurred_at, namespace)
        return await process_turn(store, graph, user_id, turn)
