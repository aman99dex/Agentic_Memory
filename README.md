# Agentic Memory

Long-term memory for LLM agents. It updates itself, knows when a fact stopped being true,
and fits under a token budget. Postgres + pgvector, a LangGraph write path, hybrid
retrieval, an MCP server, and evaluation on LongMemEval.

> 🚧 Under construction. See [plan.md](plan.md) for the roadmap and
> [docs/decisions](docs/decisions/) for why each choice was made.

## Dev quickstart
```bash
uv sync
cp .env.example .env          # then fill in ANTHROPIC_API_KEY and API_KEY
docker compose up -d db       # Docker Desktop must be running
uv run --env-file .env python -m agentic_memory.migrate
uv run --env-file .env python -m pytest        # tests start their own throwaway Postgres
```

## Run it
```bash
# REST API on http://127.0.0.1:8000 (docs at /docs); every call needs the X-API-Key header
uv run --env-file .env python -m agentic_memory.serve

# MCP server for Claude Code: remember / recall / forget, for the user in MEMORY_USER_ID
claude mcp add agentic-memory -- uv run --directory <this repo> --env-file .env python -m agentic_memory.mcp_server
```
```bash
curl -X POST localhost:8000/memories:ingest -H "X-API-Key: $API_KEY" -H 'content-type: application/json'   -d '{"user_id":"me","session_id":"s1","user_text":"I moved to Pune."}'
curl -X POST localhost:8000/memories:search -H "X-API-Key: $API_KEY" -H 'content-type: application/json'   -d '{"user_id":"me","query":"Where do I live?"}'
```

The `archive/course-notebooks/` folder holds the DeepLearning.AI course material I learned
from. It is not part of this project; see its CREDITS.md.
