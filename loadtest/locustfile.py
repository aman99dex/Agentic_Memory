"""Load test for the REST API (ADR 0018).

    uv sync --group load
    LOAD_NAME=load-1 uv run --env-file .env locust -f loadtest/locustfile.py --headless \
        -u 50 -r 5 -t 2m --host http://127.0.0.1:8000

Every simulated user is its own tenant, seeded with a few facts, then mixes three calls. Writes
and rewritten searches call a real LLM, so they cost money and are bounded by its speed; the
`search (no rewrite)` call is the retrieval path alone (embed + Postgres), which is what the
150 ms target is about. With LOAD_NAME set, a JSON summary goes to eval/results/<name>.json and
`python -m eval.report <name>` renders it. Each user erases itself when it stops.
"""

import json
import os
import random
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

from locust import HttpUser, between, events, task

KEY = os.environ["API_KEY"]
RESULTS = Path(__file__).resolve().parents[1] / "eval" / "results"
SEED = [
    "I live in Pune and work as a data engineer.",
    "My sister Meera is getting married in March.",
    "I am allergic to peanuts and I do not eat meat.",
]
QUESTIONS = [
    "Where do I live?",
    "What am I allergic to?",
    "Who is getting married?",
    "What is my job?",
]
PETS = ["cat", "dog", "parrot", "rabbit", "turtle"]


class Tenant(HttpUser):
    wait_time = between(0.5, 1.5)

    def on_start(self) -> None:
        self.client.headers["X-API-Key"] = KEY
        self.user_id = f"load-{uuid.uuid4().hex[:10]}"
        for text in SEED:
            self._ingest(text, "ingest (seed)")

    def on_stop(self) -> None:
        self.client.delete(f"/users/{self.user_id}", name="cleanup")

    def _ingest(self, text: str, name: str) -> None:
        self.client.post(
            "/memories:ingest",
            name=name,
            json={
                "user_id": self.user_id,
                "session_id": "load",
                "user_text": text,
                "occurred_at": datetime.now(UTC).isoformat(),
            },
        )

    def _search(self, name: str, rewrite: bool) -> None:
        self.client.post(
            "/memories:search",
            name=name,
            json={
                "user_id": self.user_id,
                "query": random.choice(QUESTIONS),  # noqa: S311 (spreading load, not security)
                "rewrite": rewrite,
            },
        )

    @task(8)
    def search_without_rewrite(self) -> None:
        self._search("search (no rewrite)", rewrite=False)

    @task(1)
    def search_with_rewrite(self) -> None:
        self._search("search (LLM rewrite)", rewrite=True)

    @task(1)
    def ingest(self) -> None:
        pet = random.choice(PETS)  # noqa: S311
        self._ingest(f"I adopted a {pet} named {uuid.uuid4().hex[:6]} today.", "ingest")


@events.quitting.add_listener
def save_summary(environment, **_kwargs) -> None:
    name = os.environ.get("LOAD_NAME")
    if not name:
        return
    options = environment.parsed_options
    sha = subprocess.run(  # noqa: S603
        ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    stats = environment.stats
    summary = {
        "name": name,
        "config": {
            "users": options.num_users,
            "seconds": options.run_time,
            "host": options.host,
            "git": sha or "unknown",
        },
        "endpoints": [
            {
                "name": e.name,
                "requests": e.num_requests,
                "failures": e.num_failures,
                "p50_ms": e.get_response_time_percentile(0.5),
                "p95_ms": e.get_response_time_percentile(0.95),
                "rps": e.total_rps,
            }
            for e in stats.entries.values()
            if e.num_requests
        ],
    }
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / f"{name}.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
