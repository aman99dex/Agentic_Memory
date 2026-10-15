# Agentic Memory

Long-term memory for LLM agents. It updates itself, knows when a fact stopped being true,
and fits under a token budget. Postgres + pgvector, a LangGraph write path, hybrid
retrieval, an MCP server, and evaluation on LongMemEval.

> 🚧 Under construction. See [plan.md](plan.md) for the roadmap and
> [docs/decisions](docs/decisions/) for why each choice was made.

## Dev quickstart
```bash
uv sync
docker compose up -d db
uv run pytest
```

The `archive/course-notebooks/` folder holds the DeepLearning.AI course material I learned
from. It is not part of this project; see its CREDITS.md.
