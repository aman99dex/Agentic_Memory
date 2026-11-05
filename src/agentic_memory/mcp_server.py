"""MCP server (stdio): context / remember / recall / forget for one user (ADRs 0014, 0022).

The user is fixed by MEMORY_USER_ID, so the agent can't read or erase anyone else's memories.
The project is the directory it was started for (`--project`, else the working directory):
project facts stay in that project, user-wide facts show up everywhere.
"""

import asyncio
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.mcpserver import Context, MCPServer

from agentic_memory import config
from agentic_memory.service import MemoryService, open_service

INSTRUCTIONS = """Long-term memory of this user and this project, kept across sessions.
- At the start of a task, call `context` unless memory was already given to you.
- Call `recall` when the user refers to something from before, or before deciding something
  they may have a preference about.
- Call `remember` when the user states a lasting preference, fact, decision or change.
  Saving runs in the background, so it is cheap. Never save secrets."""

USER = "me"
PROJECT: str | None = None


@asynccontextmanager
async def lifespan(_: MCPServer[MemoryService]) -> AsyncIterator[MemoryService]:
    async with open_service() as service:  # also runs the worker for saved turns
        yield service


mcp = MCPServer("agentic-memory", instructions=INSTRUCTIONS, lifespan=lifespan)


def _service(ctx: Context) -> MemoryService:
    service = ctx.request_context.lifespan_context
    assert isinstance(service, MemoryService)
    return service


@mcp.tool()
async def context(ctx: Context) -> str:
    """What is known about the user and this project, plus the latest sessions. Cheap."""
    return await _service(ctx).context(USER, PROJECT) or "Nothing remembered yet."


@mcp.tool()
async def remember(text: str, ctx: Context) -> str:
    """Save something worth remembering (a preference, fact, decision or change). It is
    turned into facts in the background, and a newer fact replaces the one it contradicts."""
    saved = await _service(ctx).ingest(USER, "mcp", text, namespace=PROJECT)
    return "Saved." if saved.new else "Already saved."


@mcp.tool()
async def recall(query: str, ctx: Context) -> str:
    """Look up what is known about the user and this project. Each memory shows its [id]
    for use with forget."""
    r = await _service(ctx).recall(USER, query, namespace=PROJECT)
    return r.text or "Nothing found."


@mcp.tool()
async def forget(memory_id: int, ctx: Context) -> str:
    """Permanently erase a memory by its [id]. Also erases its older versions, the messages
    it came from, and every other memory learned from those same messages."""
    memories, turns = await _service(ctx).forget(USER, [memory_id])
    return f"Erased {memories} memories and {turns} source messages."


def main(project: str | None = None) -> None:
    global USER, PROJECT
    config.load()
    USER = os.environ["MEMORY_USER_ID"]
    PROJECT = config.project_namespace(project or os.getcwd())
    # psycopg's async mode can't use Windows' default (Proactor) event loop.
    loop = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    asyncio.run(mcp.run_stdio_async(), loop_factory=loop)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
