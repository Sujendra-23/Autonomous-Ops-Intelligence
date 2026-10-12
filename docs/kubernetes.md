# Kubernetes

Manifests for running the backend and worker on Kubernetes live in `deploy/k8s` (Kustomize, no Helm).
Nothing here deploys itself, and no cluster was created outside a local kind test.

```
deploy/k8s/
  base/                 namespace, ConfigMap, backend Deployment+Service, worker Deployment, HPA, PDB
  migrate/              alembic Job
  monitoring/           ServiceMonitor + PrometheusRule (optional, needs Prometheus Operator CRDs)
  overlays/kind/        dev: in-cluster pgvector Postgres + Redis, development auth, local embedder
  overlays/production/  production-shaped: managed Postgres/Redis, OIDC, example.com placeholders
  secret.example.yaml   Secret template, not in any kustomization
  smoke.sh              end-to-end check used locally and in CI
```

## What each piece does

| Object | Notes |
| --- | --- |
| `Deployment/aoi-backend` | 2 replicas, rolling update with no unavailability. One uvicorn process per pod (the image `CMD`). Non-root (10001), read-only root filesystem, all capabilities dropped, `/tmp` is an `emptyDir` for uploads. |
| Probes | Startup and liveness on `/health` (process is up), readiness on `/ready` (PostgreSQL and Redis reachable). A database outage drains the pod instead of restart-looping it. |
| `Service/aoi-backend` | Named so the Prometheus `job` label is `aoi-backend`, which `observability/rules.yml` expects. |
| `Deployment/aoi-worker` | 1 replica, `Recreate`, no probes (it has no HTTP server). Not autoscaled; see below. |
| `HorizontalPodAutoscaler/aoi-backend` | CPU 70%, 2 to 4 pods, scale-down one pod per 5 minutes after a 10-minute window. |
| `PodDisruptionBudget/aoi-backend` | `minAvailable: 1`. |
| `ConfigMap/aoi-config` | Non-secret settings. Defaults are fail-closed (`ENVIRONMENT=production`, `AUTH_MODE=oidc`, empty issuer), so a deployment must choose an overlay. |
| `Secret/aoi-secrets` | Not in the repo. Keys: `DATABASE_URL`, `MIGRATION_DATABASE_URL`, `REDIS_URL`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `CONNECTOR_ENCRYPTION_KEY`, `METRICS_TOKEN`, `INGEST_API_KEY`. See `secret.example.yaml`. The kind overlay generates a dev-only one with throwaway values. |
| `Job/aoi-migrate` | `alembic upgrade head` with the schema-owner URL. Run it before the app in a real deployment and delete the finished Job before re-applying. |
| Monitoring | A `ServiceMonitor` scrapes each backend pod with the bearer token, and the `PrometheusRule` carries the seven alerts. Pod annotations (`prometheus.io/*`) cover annotation-based discovery. |

`deploy/k8s/monitoring/prometheusrule.yaml` is generated from `observability/rules.yml`
(`python scripts/render_prometheusrule.py`); a test fails if they drift, and `promtool` in CI still
checks the original.

## Why the scaling is shaped this way

The short version, with the metrics reasoning in [observability.md](observability.md):

- The backend scales out as more single-process pods, because `/metrics` is per process.
- The cap of 4 comes from the database: a pod's pool allows 30 connections (10 plus 20 overflow).
- The HPA uses CPU only, which is a weak signal for an LLM-bound service; treat it as a burst valve.
- The worker is a single replica because its scheduler takes no lock outside SaaS mode.
- The frontend is not included. Build it from `frontend/Dockerfile` and put it behind your ingress.

## Try it locally on kind

```sh
docker build -t aoi-backend:kind backend
kind create cluster --name aoi
kind load docker-image aoi-backend:kind --name aoi
kubectl apply -k deploy/k8s/overlays/kind
deploy/k8s/smoke.sh kind-aoi
```

`smoke.sh` waits for the migration Job and the rollouts, then through `kubectl port-forward` checks
`/health`, `/ready`, that `/metrics` returns 401 without the token and the metric families with it,
stores and reads back a transcript (no LLM call: `sync_extract=false`), and confirms the worker's
scheduler is running. The worker and migration Job may restart a few times at the very start while
Postgres initialises; they settle on their own.

The HPA shows `<unknown>` CPU on kind because there is no metrics-server; install one to see it scale.

## Production notes

- Replace the `example.com` values and the image name in `overlays/production`, create `aoi-secrets`
  out of band, then apply `base` objects, run the migrate Job, and wait for it before the rollout.
- Use managed PostgreSQL 17+ with pgvector and a `NOSUPERUSER NOBYPASSRLS` runtime role as described in
  [production-release.md](production-release.md). `DATABASE_URL` is the runtime role and
  `MIGRATION_DATABASE_URL` the schema owner.
- Ingress, TLS, network policies and a metrics-server are left to the cluster.

## Checks

`backend/tests/test_k8s_manifests.py` asserts the choices above without a cluster. The `kubernetes` job
in `.github/workflows/release-checks.yml` renders every overlay through kubeconform, checks the
generated PrometheusRule is current, builds the image, creates a kind cluster, applies the kind
overlay and runs `smoke.sh`.
