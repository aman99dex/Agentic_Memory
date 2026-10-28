"""A drop-in LLM wrapper that remembers every answer by (model, schema, prompt) (ADR 0007).

Same prompt, same answer: reruns and ablations that repeat a call cost nothing, and a result
can be reproduced. It also caps how many calls are in flight, so a big run can't hammer the API.
"""

import asyncio
import hashlib
import sqlite3
from pathlib import Path
from typing import Any

from pydantic import BaseModel


class CachedLLM:
    def __init__(self, llm: Any, name: str, path: Path, max_in_flight: int = 16) -> None:
        self._llm, self.name = llm, name
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS calls (key TEXT PRIMARY KEY, value TEXT)")
        self._gate = asyncio.Semaphore(max_in_flight)
        self.hits = self.misses = 0

    def _key(self, kind: str, prompt: str) -> str:
        return hashlib.sha256(f"{self.name}\0{kind}\0{prompt}".encode()).hexdigest()

    async def _call(self, kind: str, prompt: str, run: Any) -> str:
        key = self._key(kind, prompt)
        if row := self._db.execute("SELECT value FROM calls WHERE key = ?", (key,)).fetchone():
            self.hits += 1
            return str(row[0])
        async with self._gate:
            value: str = await run(prompt)
        self.misses += 1
        self._db.execute("INSERT OR REPLACE INTO calls VALUES (?, ?)", (key, value))
        self._db.commit()
        return value

    async def ainvoke(self, prompt: str) -> str:
        """Plain text in, plain text out (answers and judging)."""

        async def run(p: str) -> str:
            # .text keeps only the text blocks; .content can also hold thinking blocks
            return str((await self._llm.ainvoke(p)).text)

        return await self._call("text-v2", prompt, run)  # v2: earlier entries held raw block lists

    def with_structured_output(self, schema: type[BaseModel]) -> "_Structured":
        return _Structured(self, schema)


class _Structured:
    def __init__(self, cache: CachedLLM, schema: type[BaseModel]) -> None:
        self._cache, self._schema = cache, schema
        self._bound = cache._llm.with_structured_output(schema)

    async def ainvoke(self, prompt: str) -> Any:
        async def run(p: str) -> str:
            return str((await self._bound.ainvoke(p)).model_dump_json())

        raw = await self._cache._call(self._schema.__name__, prompt, run)
        return self._schema.model_validate_json(raw)
