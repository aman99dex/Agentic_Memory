"""`agentic-memory`: one command for every way of running it (ADRs 0019-0023).

    agentic-memory setup claude-code     install the Claude Code plugin (hooks + MCP server)
    agentic-memory status                where the data is, what is stored, what failed
    agentic-memory context [--project D] what a new session would be told
    agentic-memory recall "question"     search, as an agent would
    agentic-memory import turns.jsonl    queue past conversations, then process them
    agentic-memory serve | mcp | worker  the REST API, the MCP server, the background worker
    agentic-memory keys create <tenant>  a tenant API key for the REST API (shown once)
    agentic-memory hook <event>          called by Claude Code, reads its JSON on stdin

Imports stay inside each command: a hook that only stores a turn must not pay for LangChain.
"""

import argparse
import asyncio
import io
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import sys
from collections.abc import Coroutine
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agentic_memory import config

if TYPE_CHECKING:
    from agentic_memory.service import NewTurn

PLUGIN = "agentic-memory@agentic-memory"
MARKETPLACE = "aman99dex/Agentic_Memory"
ASSISTANT_CHARS = 8000  # a long reply is clipped before it is stored
CONFIG_TEMPLATE = """# agentic-memory settings. Real environment variables override these.
# The model that turns conversations into memories. Any LangChain provider works,
# e.g. openai:gpt-5-mini (pip install langchain-openai) or ollama:llama3.3 for fully local.
ANTHROPIC_API_KEY={key}
EXTRACT_MODEL=anthropic:claude-haiku-5-5
QUERY_MODEL=anthropic:claude-haiku-5-5
# Who the memories belong to on this machine.
MEMORY_USER_ID=me
"""


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    # psycopg's async mode can't use Windows' default (Proactor) event loop.
    loop = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    return asyncio.run(coro, loop_factory=loop)


def _user() -> str:
    return os.environ["MEMORY_USER_ID"]


def _namespace(project: str | None) -> str | None:
    return config.project_namespace(project) if project else None


# ---------------------------------------------------------------- Claude Code hooks (ADR 0022)


def _read_stdin() -> dict[str, Any]:
    # utf-8-sig: Windows shells may put a byte-order mark in front of piped text.
    raw = sys.stdin.buffer.read().decode("utf-8-sig", errors="replace")
    data: dict[str, Any] = json.loads(raw) if raw.strip() else {}
    return data


def _prompt_file(session_id: str) -> Path:
    safe = re.sub(r"[^\w-]", "_", session_id)[:100] or "unknown"
    return config.home() / "hooks" / f"{safe}.json"


def spawn_worker() -> None:
    """Drain the queue in a separate process that outlives this hook, and Claude Code itself.

    A hook process ends with its session (`claude -p` exits right after answering, and takes
    even async hooks with it), so the slow LLM work can't run inside the hook (ADR 0022).
    Leases make extra workers harmless: one works, the others find nothing to claim.
    """
    cmd = [sys.executable, "-m", "agentic_memory.cli", "worker", "--once"]
    quiet: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        try:  # leave Claude Code's job object too, or closing it would end the worker
            breakaway = flags | subprocess.CREATE_BREAKAWAY_FROM_JOB
            subprocess.Popen(cmd, creationflags=breakaway, **quiet)  # noqa: S603
            return
        except OSError:
            pass  # the job forbids breaking away; detached is the best we can do
        subprocess.Popen(cmd, creationflags=flags, **quiet)  # noqa: S603
    else:
        subprocess.Popen(cmd, start_new_session=True, **quiet)  # noqa: S603


async def _hook_session_start(event: dict[str, Any]) -> str:
    from agentic_memory.context import context  # noqa: PLC0415
    from agentic_memory.db import open_store  # noqa: PLC0415

    async with open_store() as store:
        text = await context(store, _user(), _namespace(event.get("cwd") or os.getcwd()))
        if await store.pending_turns(_user(), 1):
            spawn_worker()  # turns left by a session that ended before its worker finished
    if not text:
        return ""
    return (
        "Memory from earlier sessions (agentic-memory). Treat it as background, and prefer "
        "what the user says now if it disagrees.\n\n" + text
    )


async def _hook_stop(event: dict[str, Any]) -> None:
    """Pair the prompt saved at UserPromptSubmit with Claude's final reply and store the turn
    (no LLM, under a second), then hand the processing to a detached worker."""
    path = _prompt_file(str(event.get("session_id", "")))
    reply = str(event.get("last_assistant_message") or "")
    if not path.exists() or not reply:
        return
    saved = json.loads(path.read_text(encoding="utf-8"))
    from agentic_memory.db import open_store  # noqa: PLC0415

    async with open_store() as store:  # the turn is safe from here on
        await store.add_turn(
            _user(),
            str(event.get("session_id")),
            saved["prompt"],
            reply[:ASSISTANT_CHARS],
            datetime.fromisoformat(saved["at"]),
            _namespace(event.get("cwd") or saved.get("cwd")),
        )
    path.unlink(missing_ok=True)
    spawn_worker()


def hook(event_name: str) -> int:
    """Never fails the session: errors go to the log, and the exit code is always 0."""
    log = logging.getLogger("agentic_memory.hook")
    try:
        event = _read_stdin()
        log.info("hook %s, fields: %s", event_name, sorted(event))  # names only, no content
        if event_name == "session-start":
            text = run(_hook_session_start(event))
            if text:
                sys.stdout.buffer.write(text.encode("utf-8"))
        elif event_name == "prompt":
            prompt = str(event.get("prompt") or "").strip()
            if prompt:
                path = _prompt_file(str(event.get("session_id", "")))
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps(
                        {
                            "prompt": prompt,
                            "at": datetime.now(UTC).isoformat(),
                            "cwd": event.get("cwd"),
                        }
                    ),
                    encoding="utf-8",
                )
        elif event_name == "stop":
            run(_hook_stop(event))
    except Exception:
        logging.getLogger("agentic_memory.hook").exception("hook %s failed", event_name)
    return 0


# ---------------------------------------------------------------- setup


def setup_claude_code(source: str) -> int:
    home = config.home()
    home.mkdir(parents=True, exist_ok=True)
    cfg = home / "config.env"
    if not cfg.exists():
        cfg.write_text(
            CONFIG_TEMPLATE.format(key=os.environ.get("ANTHROPIC_API_KEY", "")), encoding="utf-8"
        )
        print(f"wrote {cfg}")
    claude = shutil.which("claude")
    if claude is None:
        print("`claude` is not on PATH: install Claude Code first.", file=sys.stderr)
        return 1
    for args in (
        ["plugin", "marketplace", "add", source],
        ["plugin", "install", PLUGIN, "--scope", "user", "-y"],
    ):
        print("$ claude " + " ".join(args), flush=True)  # before the subprocess's own output
        if subprocess.run([claude, *args], check=False).returncode != 0:  # noqa: S603
            print("that step failed; run it yourself from a normal terminal", file=sys.stderr)
            return 1
    config.load()
    if not os.environ.get("ANTHROPIC_API_KEY") and os.environ["EXTRACT_MODEL"].startswith(
        "anthropic:"
    ):
        print(f"\nOne more step: put your ANTHROPIC_API_KEY in {cfg}")
    print("\nDone. Start a new Claude Code session: it now remembers across sessions.")
    print("Check on it any time with `agentic-memory status`.")
    return 0


# ---------------------------------------------------------------- everyday commands


async def _status() -> None:
    from agentic_memory.db import open_store  # noqa: PLC0415

    print(f"data:  {config.database_url()}")
    print(f"user:  {_user()}")
    async with open_store() as store:
        for key, value in (await store.stats(_user())).items():
            print(f"{key + ':':<16}{value}")


async def _context(project: str | None) -> None:
    from agentic_memory.context import context  # noqa: PLC0415
    from agentic_memory.db import open_store  # noqa: PLC0415

    async with open_store() as store:
        print(await context(store, _user(), _namespace(project)) or "Nothing remembered yet.")


async def _recall(query: str, project: str | None, rewrite: bool) -> None:
    from agentic_memory.service import open_service  # noqa: PLC0415

    async with open_service(run_worker=False) as service:
        r = await service.recall(_user(), query, namespace=_namespace(project), rewrite=rewrite)
        print(r.text or "Nothing found.")


def _read_turns(file: str) -> list["NewTurn"]:
    from agentic_memory.service import NewTurn  # noqa: PLC0415

    turns = []
    for n, line in enumerate(Path(file).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not row.get("user_text"):
            raise SystemExit(f"line {n}: user_text is missing or empty")
        when = datetime.fromisoformat(row["occurred_at"])
        turns.append(
            NewTurn(
                str(row.get("session_id", "import")),
                row["user_text"],
                row.get("assistant_text", ""),
                when if when.tzinfo else when.replace(tzinfo=UTC),
            )
        )
    return turns


async def _import(turns: list["NewTurn"], project: str | None) -> None:
    from agentic_memory.service import open_service  # noqa: PLC0415

    async with open_service(run_worker=False) as service:
        stored, dupes = await service.import_turns(_user(), turns, _namespace(project))
        print(f"queued {stored} turns ({dupes} were already stored); processing...")
        print(f"processed {await service.worker.drain()} turns")


async def _worker(once: bool) -> None:
    from agentic_memory.service import open_service  # noqa: PLC0415

    async with open_service(run_worker=False) as service:
        if once:
            print(f"processed {await service.worker.drain()} turns")
        else:
            await service.worker.run_forever()


async def _keys(action: str, tenant: str) -> None:
    from agentic_memory.db import open_store  # noqa: PLC0415
    from agentic_memory.service import key_hash  # noqa: PLC0415

    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", tenant):
        raise SystemExit("tenant: lowercase letters, digits and dashes, up to 63 characters")
    async with open_store() as store:
        if action == "create":
            key = "am_" + secrets.token_urlsafe(32)
            await store.add_api_key(tenant, key_hash(key))
            print(f"API key for {tenant} (shown once, store it now):\n{key}")
        else:
            print(f"revoked {await store.revoke_api_keys(tenant, datetime.now(UTC))} keys")


def _serve() -> None:
    import uvicorn  # noqa: PLC0415

    from agentic_memory.api import create_app  # noqa: PLC0415

    server = uvicorn.Server(
        uvicorn.Config(
            create_app(),
            host=os.environ.get("HOST", "127.0.0.1"),
            port=int(os.environ.get("PORT", "8000")),
        )
    )
    run(server.serve())


def _setup_logging() -> None:
    """Our own logger, to a file in the home folder: stdout belongs to hooks and the MCP
    protocol. Where no home folder can be made (a container user without one), to stderr,
    which is where a container's logs belong anyway."""
    logger = logging.getLogger("agentic_memory")
    logger.setLevel(logging.INFO)
    handler: logging.Handler
    try:
        config.home().mkdir(parents=True, exist_ok=True)
        path = config.home() / "agentic-memory.log"
        target = str(path.resolve())
        if any(getattr(h, "baseFilename", None) == target for h in logger.handlers):
            return
        handler = logging.FileHandler(path, encoding="utf-8")
    except OSError:
        if any(type(h) is logging.StreamHandler for h in logger.handlers):
            return
        handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    logger.addHandler(handler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentic-memory", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("setup", help="install into an agent")
    p.add_argument("target", choices=["claude-code"])
    p.add_argument("--source", default=MARKETPLACE, help="GitHub owner/repo or a local checkout")
    sub.add_parser("status", help="where the data is and what is in it")
    p = sub.add_parser("context", help="what a new session would be told")
    p.add_argument("--project", default=os.getcwd())
    p = sub.add_parser("recall", help="search memories")
    p.add_argument("query")
    p.add_argument(
        "--no-rewrite",
        dest="rewrite",
        action="store_false",
        help="search the words as typed: no LLM call, about 2 s faster, no time filter",
    )
    p.add_argument("--project", default=os.getcwd())
    p = sub.add_parser("import", help="queue past turns from a JSONL file, then process them")
    p.add_argument("file", help="lines of {session_id, user_text, assistant_text, occurred_at}")
    p.add_argument("--project", default=None, help="the project these turns belong to")
    p = sub.add_parser("worker", help="process saved turns in the background")
    p.add_argument("--once", action="store_true", help="drain the queue, then exit")
    sub.add_parser("serve", help="the REST API (needs API_KEY or tenant keys)")
    p = sub.add_parser("mcp", help="the MCP server on stdio")
    p.add_argument("--project", default=None)
    sub.add_parser("migrate", help="apply Postgres migrations (SQLite migrates itself)")
    p = sub.add_parser("keys", help="tenant API keys for the REST API")
    p.add_argument("action", choices=["create", "revoke"])
    p.add_argument("tenant")
    p = sub.add_parser("hook", help="called by Claude Code hooks")
    p.add_argument("event", choices=["session-start", "prompt", "stop"])
    args = parser.parse_args(argv)

    # Plain "\n" even on Windows: other programs (Orbis, scripts) read this output, and a
    # Windows text stream would turn every line ending into "\r\n".
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(newline="\n")
    config.load()
    _setup_logging()
    match args.command:
        case "hook":
            return hook(args.event)
        case "setup":
            return setup_claude_code(args.source)
        case "status":
            run(_status())
        case "context":
            run(_context(args.project))
        case "recall":
            run(_recall(args.query, args.project, args.rewrite))
        case "import":
            run(_import(_read_turns(args.file), args.project))
        case "worker":
            run(_worker(args.once))
        case "serve":
            _serve()
        case "mcp":
            from agentic_memory.mcp_server import main as mcp_main  # noqa: PLC0415

            mcp_main(args.project)
        case "migrate":
            import psycopg  # noqa: PLC0415

            from agentic_memory.migrate import migrate  # noqa: PLC0415

            with psycopg.connect(config.database_url(), autocommit=True) as conn:
                print("applied:", ", ".join(migrate(conn)) or "nothing, already up to date")
        case "keys":
            run(_keys(args.action, args.tenant))
    return 0


if __name__ == "__main__":
    sys.exit(main())
