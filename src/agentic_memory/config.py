"""Where local data lives and how settings load (ADR 0019).

Local mode keeps everything in one folder: AGENTIC_MEMORY_HOME, or ~/.agentic-memory.
- memory.db: the SQLite database
- config.env: KEY=value settings (the LLM key, models). Real environment variables win.
"""

import os
import sys
from pathlib import Path

DEFAULTS = {
    "EXTRACT_MODEL": "anthropic:claude-haiku-5-5",
    "QUERY_MODEL": "anthropic:claude-haiku-5-5",
    "MEMORY_USER_ID": "me",
}


def home() -> Path:
    return Path(os.environ.get("AGENTIC_MEMORY_HOME") or Path.home() / ".agentic-memory")


def load() -> None:
    """Read config.env into the environment (never overriding), then fill in the defaults."""
    path = home() / "config.env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and key and not key.startswith("#"):
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    for key, value in DEFAULTS.items():
        os.environ.setdefault(key, value)


def database_url() -> str:
    """DATABASE_URL if set (postgresql://... or sqlite:///path), else the local SQLite file."""
    return os.environ.get("DATABASE_URL") or f"sqlite:///{(home() / 'memory.db').as_posix()}"


def project_namespace(directory: str | os.PathLike[str]) -> str:
    """The namespace of the project a directory belongs to: its git root, else the directory.

    A path is unique on one machine, where local mode lives; two repos both called "api"
    must not share memories.
    """
    start = Path(directory).resolve()
    root = next((p for p in (start, *start.parents) if (p / ".git").exists()), start)
    text = root.as_posix()
    return text.lower() if sys.platform == "win32" else text  # Windows paths ignore case
