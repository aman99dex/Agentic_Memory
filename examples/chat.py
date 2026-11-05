"""A product's chat reply with long-term memory, in about 20 lines (ADR 0023).

    agentic-memory serve                                  # the memory server (API_KEY set)
    python examples/chat.py alice "I'm vegetarian and I just moved to Pune."
    python examples/chat.py alice "Where should I go for dinner tonight?"

Needs `pip install agentic-memory anthropic`, MEMORY_URL / MEMORY_API_KEY for the memory
server, and ANTHROPIC_API_KEY for the model that replies.
"""

import sys

import anthropic

from agentic_memory.client import Memory

user_id, message = sys.argv[1], sys.argv[2]
memory = Memory(user_id, namespace="food-app")  # this app's memories, plus user-wide ones

known = memory.context() + "\n\n" + memory.search(message, mode="facts", rewrite=False).text
response = anthropic.Anthropic().messages.create(
    model="claude-haiku-5-5",
    max_tokens=1000,
    system=f"You are a food assistant. What you remember about this user:\n{known}",
    messages=[{"role": "user", "content": message}],
)
reply = "".join(block.text for block in response.content if block.type == "text")
memory.add(message, reply)  # returns at once; the server learns from it in the background
print(reply)
