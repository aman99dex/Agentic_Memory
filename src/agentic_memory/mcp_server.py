"""MCP server (stdio) with remember / recall / forget for one user (ADR 0014).

The user is fixed by MEMORY_USER_ID, so the agent can't read or erase anyone else's memories.
"""

import asyncio
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.mcpserver import Context, MCPServer

from agentic_memory.service import MemoryService, open_service

USER = os.environ.get("MEMORY_USER_ID", "me")


@asynccontextmanager
async def lifespan(_: MCPServer[MemoryService]) -> AsyncIterator[MemoryService]:
    async with open_service() as service:
        yield service


mcp = MCPServer("agentic-memory", lifespan=lifespan)


def _service(ctx: Context) -> MemoryService:
    service = ctx.request_context.lifespan_context
    assert isinstance(service, MemoryService)
    return service


@mcp.tool()
async def remember(text: str, ctx: Context) -> str:
    """Save something worth remembering about the user (a fact, preference, plan or change)."""
    steps = await _service(ctx).ingest(USER, "mcp", text)
    done = [f"{s['action']}: {s['fact']}" for s in steps if s["action"] != "NOOP"]
    return "\n".join(done) or "Nothing new to remember."


@mcp.tool()
async def recall(query: str, ctx: Context) -> str:
    """Look up what is known about the user. Each fact shows its [id] for use with forget."""
    r = await _service(ctx).recall(USER, query)
    return r.text or "Nothing found."


@mcp.tool()
async def forget(memory_id: int, ctx: Context) -> str:
    """Permanently erase a fact by its [id] from recall. Also erases its older versions, the
    messages it came from, and every other fact learned from those same messages."""
    memories, turns = await _service(ctx).forget(USER, [memory_id])
    return f"Erased {memories} fact(s) and {turns} source message(s)."


if __name__ == "__main__":
    # psycopg's async mode can't use Windows' default (Proactor) event loop.
    loop = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    asyncio.run(mcp.run_stdio_async(), loop_factory=loop)
