"""Python SDK: a thin client for the REST API (ADR 0014).

    from agentic_memory.client import Memory

    mem = Memory("aman", base_url="http://127.0.0.1:8000", api_key="...")
    mem.add("I moved to Pune.")
    print(mem.search("Where do I live?").text)

`base_url` and `api_key` default to MEMORY_URL and MEMORY_API_KEY (or API_KEY).
"""

import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Literal, Self

import httpx


@dataclass(frozen=True)
class SearchResult:
    text: str  # ready to paste into a prompt
    memories: list[dict[str, Any]]
    turns: list[dict[str, Any]]
    as_of: str | None  # the past moment the question asked about, if any
    search_text: str  # the question as it was actually searched


class Memory:
    def __init__(
        self,
        user_id: str,
        base_url: str | None = None,
        api_key: str | None = None,
        *,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.user_id = user_id
        self._http = httpx.Client(
            base_url=base_url or os.environ.get("MEMORY_URL", "http://127.0.0.1:8000"),
            headers={"X-API-Key": api_key or _key_from_env()},
            timeout=timeout,
            transport=transport,
        )

    def _send(self, method: str, url: str, **kwargs: Any) -> Any:
        response = self._http.request(method, url, **kwargs)
        response.raise_for_status()
        return response.json()

    def add(
        self,
        text: str,
        reply: str = "",
        *,
        session_id: str = "sdk",
        occurred_at: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Remember one round of conversation. Returns what was done with each fact found.

        The same round is stored once, keyed on `occurred_at`: when retrying a failed call,
        pass the same `occurred_at` as the first attempt (the default is the current time).
        """
        when = occurred_at or datetime.now(UTC)
        body = {
            "user_id": self.user_id,
            "session_id": session_id,
            "user_text": text,
            "assistant_text": reply,
            "occurred_at": when.isoformat(),
        }
        steps: list[dict[str, Any]] = self._send("POST", "/memories:ingest", json=body)["steps"]
        return steps

    def search(
        self,
        query: str,
        *,
        context: Sequence[str] = (),
        budget: int = 1500,
        mode: Literal["facts", "turns", "both"] = "both",
        rewrite: bool = True,
    ) -> SearchResult:
        body = {
            "user_id": self.user_id,
            "query": query,
            "context": list(context),
            "budget": budget,
            "mode": mode,
            "rewrite": rewrite,
        }
        r = self._send("POST", "/memories:search", json=body)
        return SearchResult(r["text"], r["memories"], r["turns"], r["as_of"], r["search_text"])

    def history(self, memory_id: int) -> list[dict[str, Any]]:
        """Every version of one fact, oldest first."""
        versions: list[dict[str, Any]] = self._send(
            "GET", f"/memories/{memory_id}/history", params={"user_id": self.user_id}
        )
        return versions

    def forget(self, memory_ids: Sequence[int] | None = None) -> dict[str, int]:
        """Hard delete (ADR 0014). With no ids, erases everything this user has."""
        if memory_ids is None:
            deleted: dict[str, int] = self._send("DELETE", f"/users/{self.user_id}")
        else:
            deleted = self._send(
                "DELETE", "/memories", params={"user_id": self.user_id, "ids": list(memory_ids)}
            )
        return deleted

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


def _key_from_env() -> str:
    return os.environ.get("MEMORY_API_KEY") or os.environ.get("API_KEY", "")
