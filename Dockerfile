# One image for everything: the API (default), the migration job, and the nightly decay job.
#   docker build -t agentic-memory .
#   docker run --env-file .env -p 8000:8000 agentic-memory
#   docker run --env-file .env agentic-memory python -m agentic_memory.migrate
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 HOST=0.0.0.0 PORT=8000 FASTEMBED_CACHE_PATH=/models
WORKDIR /app

# Dependencies first: this layer only rebuilds when the lockfile changes.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --extra server --no-install-project

COPY src ./src
RUN uv sync --locked --no-dev --extra server

# Download the embedding model now, so a cold start doesn't spend seconds fetching it (ADR 0009).
# The model loads on first use (ADR 0022), so embed something to fetch it here.
RUN python -c "from agentic_memory.embedder import Embedder; Embedder().embed(['warm up'])"

RUN useradd --system --no-create-home app && chown -R app /models
USER app
EXPOSE 8000
CMD ["python", "-m", "agentic_memory.serve"]
