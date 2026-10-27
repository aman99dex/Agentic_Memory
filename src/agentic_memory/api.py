"""REST API (ADR 0014). Every route needs the X-API-Key header; input is validated here."""

import hmac
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from pydantic import AfterValidator, BaseModel, Field

from agentic_memory.read_path import ReturnMode
from agentic_memory.service import MemoryService, open_service

UserId = Annotated[str, Field(min_length=1, max_length=128)]
Text = Annotated[str, Field(max_length=20_000)]


def _aware(d: datetime | None) -> datetime | None:
    return d.replace(tzinfo=UTC) if d and d.tzinfo is None else d  # bare timestamps mean UTC


When = Annotated[datetime | None, AfterValidator(_aware)]


class IngestIn(BaseModel):
    user_id: UserId
    session_id: UserId
    user_text: Annotated[str, Field(min_length=1, max_length=20_000)]
    assistant_text: Text = ""
    occurred_at: When = None  # send it on retries: it is part of the idempotency key


class SearchIn(BaseModel):
    user_id: UserId
    query: Annotated[str, Field(min_length=1, max_length=2_000)]
    context: list[Annotated[str, Field(max_length=2_000)]] = Field(default=[], max_length=20)
    budget: int = Field(default=1500, ge=50, le=20_000)
    mode: ReturnMode = "both"


def create_app(service: MemoryService | None = None, api_key: str | None = None) -> FastAPI:
    key = api_key or os.environ.get("API_KEY", "")
    if not key:
        raise RuntimeError("set API_KEY: the API refuses to start unauthenticated")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if service is not None:
            yield
        else:
            async with open_service() as opened:
                app.state.service = opened
                yield

    def check_key(x_api_key: Annotated[str, Header()] = "") -> None:
        if not hmac.compare_digest(x_api_key.encode(), key.encode()):
            raise HTTPException(401, "bad or missing X-API-Key")

    def get_service(request: Request) -> MemoryService:
        s: MemoryService = request.app.state.service
        return s

    Svc = Annotated[MemoryService, Depends(get_service)]
    app = FastAPI(title="Agentic Memory", lifespan=lifespan, dependencies=[Depends(check_key)])

    @app.post("/memories:ingest")
    async def ingest(body: IngestIn, s: Svc) -> dict[str, object]:
        steps = await s.ingest(
            body.user_id, body.session_id, body.user_text, body.assistant_text, body.occurred_at
        )
        return {"steps": steps}

    @app.post("/memories:search")
    async def search(body: SearchIn, s: Svc) -> dict[str, object]:
        r = await s.recall(
            body.user_id, body.query, context=body.context, budget=body.budget, mode=body.mode
        )
        return {
            "text": r.text,
            "as_of": r.query.as_of,
            "search_text": r.query.search_text,
            "memories": r.memories,
            "turns": r.turns,
        }

    @app.get("/memories/{memory_id}/history")
    async def history(memory_id: int, user_id: Annotated[UserId, Query()], s: Svc) -> object:
        versions = await s.history(user_id, memory_id)
        if not versions:
            raise HTTPException(404, "no such memory")
        return versions

    @app.delete("/memories")
    async def forget(
        user_id: Annotated[UserId, Query()],
        s: Svc,
        ids: Annotated[list[int], Query(min_length=1, max_length=100)],
    ) -> dict[str, int]:
        memories, turns = await s.forget(user_id, ids)
        return {"memories": memories, "turns": turns}

    @app.delete("/users/{user_id}")
    async def forget_user(user_id: UserId, s: Svc) -> dict[str, int]:
        memories, turns = await s.forget(user_id, None)
        return {"memories": memories, "turns": turns}

    if service is not None:
        app.state.service = service  # set now: test clients don't run the lifespan
    return app
