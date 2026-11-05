"""Local mode end to end through the `agentic-memory` command (ADRs 0019, 0022): SQLite in a
temp folder, the Claude Code hooks fed the JSON Claude Code sends them.

The last test uses the real model (run with --env-file .env).
"""

import asyncio
import io
import json
import os
import sqlite3
import sys
import time

import pytest

from agentic_memory import cli, config
from agentic_memory.embedder import Embedder
from agentic_memory.sqlite_store import SqliteStore


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTIC_MEMORY_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("DATABASE_URL", raising=False)  # local mode: the SQLite default
    monkeypatch.setenv("MEMORY_USER_ID", "me")
    return tmp_path / "home"


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "shop"
    (root / ".git").mkdir(parents=True)
    (root / "src").mkdir()
    return root


def hook(monkeypatch, capsysbinary, event_name, payload) -> str:
    raw = json.dumps(payload).encode()
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8"))
    assert cli.main(["hook", event_name]) == 0  # a hook never fails the session
    return capsysbinary.readouterr().out.decode("utf-8")


def test_project_namespace_is_the_git_root(repo):
    assert config.project_namespace(repo / "src") == config.project_namespace(repo)
    assert config.project_namespace(repo).endswith("/shop")


def test_session_start_tells_what_is_known_without_any_llm(home, repo, monkeypatch, capsysbinary):
    assert hook(monkeypatch, capsysbinary, "session-start", {"cwd": str(repo)}) == ""

    async def seed():
        store = SqliteStore(home / "memory.db", Embedder())
        ns = config.project_namespace(repo)
        await store.add("me", "The user wants type hints everywhere.", [1], _t())
        await store.add("me", "The project deploys to Cloud Run.", [2], _t(), ns)
        await store.put_derived("me", "episode", "Added the checkout page.", [3], _t(), ns, "s0")
        store.close()

    asyncio.run(seed())
    out = hook(monkeypatch, capsysbinary, "session-start", {"cwd": str(repo / "src")})
    assert "type hints" in out and "Cloud Run" in out and "checkout page" in out
    elsewhere = hook(monkeypatch, capsysbinary, "session-start", {"cwd": str(home)})
    assert "type hints" in elsewhere and "Cloud Run" not in elsewhere


def test_commands_run_where_no_home_folder_can_be_made(tmp_path, monkeypatch, capsys):
    # The Docker image runs as a user with no home directory; the API crashed on start once.
    blocker = tmp_path / "a-file"
    blocker.write_text("not a folder")
    monkeypatch.setenv("AGENTIC_MEMORY_HOME", str(blocker / "home"))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'm.db').as_posix()}")
    assert cli.main(["status"]) == 0
    assert "queued:" in capsys.readouterr().out


def test_hooks_swallow_bad_input_and_a_stop_without_a_prompt(home, monkeypatch, capsysbinary):
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"{not json"), encoding="utf-8"))
    assert cli.main(["hook", "session-start"]) == 0
    assert hook(monkeypatch, capsysbinary, "stop", {"session_id": "x"}) == ""
    assert "hook session-start failed" in (home / "agentic-memory.log").read_text()


@pytest.mark.skipif("ANTHROPIC_API_KEY" not in os.environ, reason="needs a real LLM key")
def test_a_claude_code_session_is_remembered_in_the_next_one(home, repo, monkeypatch, capsysbinary):
    session = {"session_id": "abc-123", "cwd": str(repo)}
    hook(monkeypatch, capsysbinary, "prompt", session | {
        "prompt": "Add a /health endpoint to src/app.py. FYI this project deploys to Cloud Run. "
                  "And for me personally: always use type hints in Python, never untyped code.",
    })  # fmt: skip
    hook(monkeypatch, capsysbinary, "stop", session | {
        "last_assistant_message": "Added GET /health to src/app.py; it returns {\"ok\": true} "
                                  "with full type hints. Noted: Cloud Run, and type hints always.",
    })  # fmt: skip

    async def check():
        store = SqliteStore(home / "memory.db", Embedder())
        stats = await store.stats("me")
        store.close()
        return stats

    def worker_busy() -> bool:  # it holds the lease until the summaries are written too
        with sqlite3.connect(home / "memory.db") as db:
            return db.execute("SELECT count(*) FROM ingest_leases").fetchone()[0] > 0

    # The hook only stored the turn; a detached worker process does the LLM work.
    assert asyncio.run(check())["turns"] == 1
    deadline = time.monotonic() + 120
    time.sleep(2)  # let the worker start
    while (asyncio.run(check())["queued"] or worker_busy()) and time.monotonic() < deadline:
        time.sleep(1)
    stats = asyncio.run(check())
    assert stats["queued"] == 0 and stats["failed"] == 0, stats
    assert stats["current facts"] >= 2 and stats["sessions"] == 1 and stats["profiles"] == 2

    here = hook(monkeypatch, capsysbinary, "session-start", {"cwd": str(repo), "source": "startup"})
    assert "Cloud Run" in here and "type hint" in here.lower()
    assert "/health" in here  # the session summary: what was done last time
    elsewhere = hook(monkeypatch, capsysbinary, "session-start", {"cwd": str(home)})
    assert "type hint" in elsewhere.lower() and "Cloud Run" not in elsewhere

    assert cli.main(["recall", "What did we build last session?", "--project", str(repo)]) == 0
    assert "health" in capsysbinary.readouterr().out.decode()


def _t():
    from datetime import UTC, datetime

    return datetime(2026, 10, 1, tzinfo=UTC)
