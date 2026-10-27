"""Run the REST API: `uv run --env-file .env python -m agentic_memory.serve` (needs API_KEY)."""

import asyncio
import os
import sys

import uvicorn

from agentic_memory.api import create_app

if __name__ == "__main__":
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(),
            host=os.environ.get("HOST", "127.0.0.1"),
            port=int(os.environ.get("PORT", 8000)),
        )
    )
    # psycopg's async mode can't use Windows' default (Proactor) event loop.
    loop = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    asyncio.run(server.serve(), loop_factory=loop)
