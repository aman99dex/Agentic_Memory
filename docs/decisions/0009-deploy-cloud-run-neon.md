# 0009. Cloud Run + Neon for the public deployment

**Status:** Accepted, 2026-10-16

## Context
The goal is the cheapest public URL that still looks production-shaped: a container,
managed Postgres with pgvector, and CI/CD. Free-tier landscape as of Oct 2026:

| Host | Free tier | Problem |
|---|---|---|
| **Google Cloud Run** | 180k vCPU-seconds and 2M requests per month | Cold starts when scaled to zero |
| Render / Koyeb | 512 MB, 0.1 CPU, sleeps when idle | Embedding model + Python is tight; Render says not to use it for production |
| Fly.io | Trial only for new orgs | Not free |
| HF Spaces | 16 GB, free | Not a real API host; disk is not persistent |

| Postgres | Free tier | pgvector | BM25 |
|---|---|---|---|
| **Neon** | 0.5 GB per project, scales to zero | 0.8.6 on PG18 | No (pg_search retired) |
| Supabase | 500 MB, pauses after about a week idle | Yes | Via replica only |
| Aiven | 1 GB | Yes | No |

## Decision
- **Cloud Run** with 1 GiB memory and `min-instances=0` (free). Bump to 1 during a demo
  or interview to avoid a cold start.
- **Neon PG18** for the database. ADR 0002 already removed the BM25 dependency, so Neon
  is fine.
- Deployment: GitHub Actions authenticates through **Workload Identity Federation** (no
  JSON keys in GitHub), builds the image, runs migrations as a separate job, then deploys.
- Local development and self-hosting use the same image through `docker compose`.

## Consequences
- The first request after idle takes a few seconds: container start plus model load plus
  Neon wake-up. This is documented, and the p95 latency target is measured warm.
- Neon's free 0.5 GB holds the demo tenant, not a LongMemEval run. Evals run against
  local Postgres.

**In an interview:** "Scale-to-zero container plus serverless Postgres costs nothing at
idle. Deploys use OIDC with no long-lived keys, and migrations are a separate step from
app startup."
