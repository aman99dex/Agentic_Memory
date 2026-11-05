"""REST API (ADRs 0014, 0020, 0023). Every route needs an X-API-Key; input is validated here.

Two kinds of key:
- API_KEY from the environment: the operator's key, the "default" tenant.
- Tenant keys from `agentic-memory keys create <tenant>`: each tenant sees only its own users,
  because its user ids are stored as "<tenant>:<user_id>" and every route goes through `uid`.
"""

import hmac
import os
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from pydantic import AfterValidator, BaseModel, Field

from agentic_memory.read_path import ReturnMode
from agentic_memory.service import MemoryService, NewTurn, open_service
from agentic_memory.store import Memory, Turn

UserId = Annotated[str, Field(min_length=1, max_length=128)]
Namespace = Annotated[str | None, Field(default=None, min_length=1, max_length=512)]
Text = Annotated[str, Field(max_length=20_000)]
UserText = Annotated[str, Field(min_length=1, max_length=20_000)]


def _aware(d: datetime | None) -> datetime | None:
    return d.replace(tzinfo=UTC) if d and d.tzinfo is None else d  # bare timestamps mean UTC


When = Annotated[datetime | None, AfterValidator(_aware)]
Required = Annotated[datetime, AfterValidator(_aware)]


class IngestIn(BaseModel):
    user_id: UserId
    session_id: UserId
    user_text: UserText
    assistant_text: Text = ""
    occurred_at: When = None  # send it on retries: it is part of the idempotency key
    namespace: Namespace = None
    wait: bool = False  # true: process now and return what was done with each fact


class ImportTurn(BaseModel):
    session_id: UserId
    user_text: UserText
    assistant_text: Text = ""
    occurred_at: Required  # required: it orders the history and makes a re-import a no-op


class ImportIn(BaseModel):
    user_id: UserId
    namespace: Namespace = None
    turns: list[ImportTurn] = Field(min_length=1, max_length=1000)


class SearchIn(BaseModel):
    user_id: UserId
    query: Annotated[str, Field(min_length=1, max_length=2_000)]
    context: list[Annotated[str, Field(max_length=2_000)]] = Field(default=[], max_length=20)
    budget: int = Field(default=1500, ge=50, le=20_000)
    mode: ReturnMode = "both"
    rewrite: bool = True  # false skips the LLM query rewrite: faster and free, no time filter
    namespace: Namespace = None


class ContextIn(BaseModel):
    user_id: UserId
    namespace: Namespace = None
    budget: int = Field(default=1200, ge=50, le=20_000)


def _public(items: Sequence[Memory | Turn], user_id: str) -> list[dict[str, Any]]:
    """What a caller sees: their own user id, never the tenant-prefixed one we store."""
    out = []
    for item in items:
        d = asdict(item)
        if "user_id" in d:
            d["user_id"] = user_id
        out.append(d)
    return out


def create_app(service: MemoryService | None = None, api_key: str | None = None) -> FastAPI:
    operator_key = api_key or os.environ.get("API_KEY", "")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if service is not None:
            yield
        else:
            async with open_service() as opened:  # also runs the background worker
                app.state.service = opened
                yield

    def get_service(request: Request) -> MemoryService:
        s: MemoryService = request.app.state.service
        return s

    Svc = Annotated[MemoryService, Depends(get_service)]

    async def tenant(s: Svc, x_api_key: Annotated[str, Header()] = "") -> str:
        if (
            x_api_key
            and operator_key
            and hmac.compare_digest(x_api_key.encode(), operator_key.encode())
        ):
            return ""
        # A hash lookup: the key itself is never stored, and timing reveals nothing useful.
        if x_api_key and (name := await s.tenant(x_api_key)):
            return name
        raise HTTPException(401, "bad or missing X-API-Key")

    Tenant = Annotated[str, Depends(tenant)]

    def uid(t: str, user_id: str) -> str:
        return f"{t}:{user_id}" if t else user_id

    app = FastAPI(title="Agentic Memory", lifespan=lifespan, dependencies=[Depends(tenant)])

    @app.post("/memories:ingest")
    async def ingest(body: IngestIn, s: Svc, t: Tenant) -> dict[str, object]:
        saved = await s.ingest(
            uid(t, body.user_id),
            body.session_id,
            body.user_text,
            body.assistant_text,
            body.occurred_at,
            namespace=body.namespace,
            wait=body.wait,
        )
        out: dict[str, object] = {"turn_id": saved.turn_id, "queued": saved.new and not body.wait}
        if body.wait:
            out["steps"] = saved.steps
        return out

    @app.post("/memories:import")
    async def import_turns(body: ImportIn, s: Svc, t: Tenant) -> dict[str, int]:
        turns = [
            NewTurn(x.session_id, x.user_text, x.assistant_text, x.occurred_at) for x in body.turns
        ]
        stored, duplicates = await s.import_turns(uid(t, body.user_id), turns, body.namespace)
        return {"stored": stored, "duplicates": duplicates}

    @app.post("/memories:search")
    async def search(body: SearchIn, s: Svc, t: Tenant) -> dict[str, object]:
        r = await s.recall(
            uid(t, body.user_id),
            body.query,
            context=body.context,
            budget=body.budget,
            mode=body.mode,
            rewrite=body.rewrite,
            namespace=body.namespace,
        )
        return {
            "text": r.text,
            "as_of": r.query.as_of,
            "search_text": r.query.search_text,
            "memories": _public(r.memories, body.user_id),
            "turns": _public(r.turns, body.user_id),
        }

    @app.post("/memories:context")
    async def get_context(body: ContextIn, s: Svc, t: Tenant) -> dict[str, str]:
        return {"text": await s.context(uid(t, body.user_id), body.namespace, body.budget)}

    @app.get("/memories/{memory_id}/history")
    async def history(
        memory_id: int, user_id: Annotated[UserId, Query()], s: Svc, t: Tenant
    ) -> object:
        versions = await s.history(uid(t, user_id), memory_id)
        if not versions:
            raise HTTPException(404, "no such memory")
        return _public(versions, user_id)

    @app.delete("/memories")
    async def forget(
        user_id: Annotated[UserId, Query()],
        s: Svc,
        t: Tenant,
        ids: Annotated[list[int], Query(min_length=1, max_length=100)],
    ) -> dict[str, int]:
        memories, turns = await s.forget(uid(t, user_id), ids)
        return {"memories": memories, "turns": turns}

    @app.delete("/users/{user_id}")
    async def forget_user(user_id: UserId, s: Svc, t: Tenant) -> dict[str, int]:
        memories, turns = await s.forget(uid(t, user_id), None)
        return {"memories": memories, "turns": turns}

    if service is not None:
        app.state.service = service  # set now: test clients don't run the lifespan
    return app
