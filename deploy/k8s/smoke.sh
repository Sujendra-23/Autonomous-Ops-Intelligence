#!/usr/bin/env bash
# Smoke test for the kind overlay: waits for the migration Job and rollouts, then exercises the API
# through the Kubernetes Service. Usage: deploy/k8s/smoke.sh [kube-context]
set -euo pipefail

CTX=${1:-$(kubectl config current-context)}
K="kubectl --context $CTX -n aoi"
PORT=${SMOKE_PORT:-18000}

$K wait --for=condition=complete job/aoi-migrate --timeout=300s
$K rollout status statefulset/postgres deployment/redis deployment/aoi-backend deployment/aoi-worker --timeout=300s

$K port-forward svc/aoi-backend "$PORT:8000" >/dev/null &
PF=$!
trap 'kill $PF 2>/dev/null || true' EXIT
for _ in $(seq 1 30); do
  curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break
  sleep 1
done

echo "health:  $(curl -fsS "http://127.0.0.1:$PORT/health")"
echo "ready:   $(curl -fsS "http://127.0.0.1:$PORT/ready")"
# /metrics requires the bearer token from the Secret (METRICS_TOKEN), and refuses without it.
TOKEN=$($K get secret aoi-secrets -o jsonpath='{.data.METRICS_TOKEN}' | base64 -d)
[ "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/metrics")" = 401 ]
curl -fsS -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:$PORT/metrics" > /tmp/aoi-metrics.txt
grep -q '^# HELP aoi_llm_requests_total' /tmp/aoi-metrics.txt
echo "metrics: 401 without token, aoi_llm_requests_total exposed with it"

# Round-trip through the migrated schema without calling an LLM (sync_extract=false).
ID=$(curl -fsS -X POST "http://127.0.0.1:$PORT/api/transcripts" \
  -H 'Content-Type: application/json' \
  -d '{"title":"k8s smoke","content":"Priya will ship it by Friday.","sync_extract":false}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
curl -fsS "http://127.0.0.1:$PORT/api/transcripts/$ID" >/dev/null
echo "db:      transcript $ID stored and read back"

$K logs deployment/aoi-worker --tail=50 | grep -q 'scheduler'
echo "worker:  scheduler running"
echo "smoke test passed"
