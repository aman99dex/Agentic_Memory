# Deploying to Cloud Run + Neon (ADR 0009)

The `deploy` workflow builds the image, runs the migrations as a Cloud Run job, then deploys the
service. It stays idle until the repository variable `GCP_PROJECT_ID` is set. Everything below is
one-time setup; none of it can be done from this repo.

## 1. Neon
Create a project on Postgres 18 and enable `pgvector`. Copy the connection string (it must end
with `?sslmode=require`).

## 2. Google Cloud
1. Enable the Cloud Run, Artifact Registry and Secret Manager APIs.
2. Create a Docker repository in Artifact Registry.
3. Create three secrets: `database-url` (the Neon string), `anthropic-api-key`, `api-key` (a long
   random string you will send as `X-API-Key`).
4. Create a service account for deploys with the roles Cloud Run Admin, Artifact Registry Writer,
   Secret Manager Secret Accessor and Service Account User.
5. Create a Workload Identity Pool and an OIDC provider for `https://token.actions.githubusercontent.com`,
   restrict it to this repository (`attribute.repository == "<owner>/<repo>"`), and let it
   impersonate the service account. This is what replaces a JSON key in GitHub.

## 3. GitHub repository variables (Settings -> Secrets and variables -> Actions -> Variables)
| Variable | Example |
|---|---|
| `GCP_PROJECT_ID` | `my-project` |
| `GCP_REGION` | `asia-south1` |
| `GCP_AR_REPO` | `agentic-memory` |
| `GCP_WIF_PROVIDER` | `projects/123/locations/global/workloadIdentityPools/gh/providers/gh` |
| `GCP_SERVICE_ACCOUNT` | `deployer@my-project.iam.gserviceaccount.com` |
| `EXTRACT_MODEL` | `anthropic:claude-haiku-5-5` |
| `QUERY_MODEL` | `anthropic:claude-haiku-5-5` |

No secret values live in GitHub; they stay in Secret Manager.

## 4. Nightly forgetting (ADR 0016)
Create a Cloud Run job from the same image with the command `python -m agentic_memory.decay` and
`DATABASE_URL` from the secret, then trigger it daily with Cloud Scheduler.

## 5. Telemetry (ADR 0018), optional
Set `OTEL_EXPORTER_OTLP_ENDPOINT` (and `PRICE_INPUT_PER_MTOK` / `PRICE_OUTPUT_PER_MTOK` for the
cost counter) on the service. Without them nothing is exported.

## Cold starts
With `min-instances=0` the first request after idle waits for the container, the model load and
Neon waking up: a few seconds. Latency figures in the README are measured warm. Set
`--min-instances=1` for a demo.
