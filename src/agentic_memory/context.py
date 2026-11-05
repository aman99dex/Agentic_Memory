"""The briefing an agent gets before its first message (ADR 0021). No LLM, no embedding.

The user profile, the project brief, and the latest sessions. Until the reflector has written
a profile, the most-used current facts stand in for it.
"""

from agentic_memory.store import Store


def tokens(text: str) -> int:
    # ponytail: 4 characters ~ 1 token. Close enough for English; swap in a real tokenizer if
    # budgets must be exact.
    return len(text) // 4 + 1


async def context(
    store: Store, user_id: str, namespace: str | None, *, budget: int = 1200, episodes: int = 3
) -> str:
    sections: list[tuple[str, list[str]]] = []
    scopes: list[tuple[str, str | None]] = [("About the user", None)]
    if namespace is not None:
        scopes.append(("About this project", namespace))
    for title, ns in scopes:
        profile = await store.derived(user_id, "profile", namespace=ns)
        if profile:
            sections.append((title, [profile.content]))
        elif facts := await store.current_facts(user_id, ns, limit=15):
            sections.append((title, [f"- {m.content}" for m in facts]))
    if recent := await store.recent_episodes(user_id, namespace, episodes):
        lines = [f"- {m.valid_from.date()}: {m.content}" for m in recent]
        sections.append(("Recent sessions, newest first", lines))
    out: list[str] = []
    used = 0
    for title, lines in sections:
        kept = []
        for line in lines:
            if used + tokens(line) > budget:
                break
            kept.append(line)
            used += tokens(line)
        if kept:
            out.append(f"{title}:\n" + "\n".join(kept))
    return "\n\n".join(out)
