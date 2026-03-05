#!/usr/bin/env bash
set -euo pipefail

# Run legacy vs v4 RAG comparison in the current docker stack.
# - Reuses running legacy backend container
# - Starts temporary v4 backend container with RAG_ORCHESTRATOR_V4=true
# - Executes comparison from inside legacy container
# - Writes report to mounted /app/data (host: ./data by default)

LEGACY_CONTAINER="telegram_rag_backend"
V4_CONTAINER="telegram_rag_backend_v4"
API_PREFIX=""
KB_ID=""
MAX_DROP="0.10"
CASES_FILE_HOST="tests/rag_eval.yaml"
REPORT_PATH_CONTAINER="/app/data/rag_compare_report.json"
KEEP_V4="false"
PRINT_JSON="true"

usage() {
  cat <<'EOF'
Usage:
  bash scripts/run_rag_compare_stack.sh [options]

Options:
  --legacy-container <name>      Legacy backend container (default: telegram_rag_backend)
  --v4-container <name>          Temporary v4 container name (default: telegram_rag_backend_v4)
  --api-prefix <path>            API prefix (default: from container BACKEND_API_PREFIX or /api/v1)
  --kb-id <id>                   Knowledge base id (optional; auto-resolve if omitted)
  --cases-file <path>            Host path to eval cases file (default: tests/rag_eval.yaml)
  --report-path <path>           Container report path (default: /app/data/rag_compare_report.json)
  --max-source-hit-drop <float>  Fail if v4 source_hit drops more than value (default: 0.10)
  --keep-v4                      Keep temporary v4 container after run
  --no-print-json                Do not print full JSON report to stdout
  -h, --help                     Show help

Examples:
  bash scripts/run_rag_compare_stack.sh
  bash scripts/run_rag_compare_stack.sh --kb-id 1 --max-source-hit-drop 0.05
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --legacy-container)
      LEGACY_CONTAINER="$2"
      shift 2
      ;;
    --v4-container)
      V4_CONTAINER="$2"
      shift 2
      ;;
    --api-prefix)
      API_PREFIX="$2"
      shift 2
      ;;
    --kb-id)
      KB_ID="$2"
      shift 2
      ;;
    --cases-file)
      CASES_FILE_HOST="$2"
      shift 2
      ;;
    --report-path)
      REPORT_PATH_CONTAINER="$2"
      shift 2
      ;;
    --max-source-hit-drop)
      MAX_DROP="$2"
      shift 2
      ;;
    --keep-v4)
      KEEP_V4="true"
      shift
      ;;
    --no-print-json)
      PRINT_JSON="false"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if ! docker ps --format '{{.Names}}' | grep -qx "$LEGACY_CONTAINER"; then
  echo "ERROR: legacy container is not running: $LEGACY_CONTAINER" >&2
  echo "Running containers:" >&2
  docker ps --format ' - {{.Names}}' >&2
  exit 2
fi

if [[ ! -f .env ]]; then
  echo "ERROR: .env not found in current directory" >&2
  exit 2
fi

NETWORK_NAME="$(docker inspect "$LEGACY_CONTAINER" --format '{{range $k,$v := .NetworkSettings.Networks}}{{println $k}}{{end}}' | head -n1 | tr -d '\r')"
IMAGE_NAME="$(docker inspect "$LEGACY_CONTAINER" --format '{{.Config.Image}}' | tr -d '\r')"
HOST_DATA_DIR="$(docker inspect "$LEGACY_CONTAINER" --format '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{println .Source}}{{end}}{{end}}' | tr -d '\r' | head -n1)"

if [[ -z "$NETWORK_NAME" || -z "$IMAGE_NAME" ]]; then
  echo "ERROR: failed to resolve docker network/image for $LEGACY_CONTAINER" >&2
  exit 2
fi

docker rm -f "$V4_CONTAINER" >/dev/null 2>&1 || true

cleanup() {
  if [[ "$KEEP_V4" != "true" ]]; then
    docker rm -f "$V4_CONTAINER" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

echo "[INFO] starting v4 container: $V4_CONTAINER"
docker run -d --rm \
  --name "$V4_CONTAINER" \
  --network "$NETWORK_NAME" \
  --volumes-from "$LEGACY_CONTAINER" \
  --env-file .env \
  -e RAG_ORCHESTRATOR_V4=true \
  "$IMAGE_NAME" \
  uvicorn backend.app:app --host 0.0.0.0 --port 8000 --workers 1 >/dev/null

echo "[INFO] waiting for v4 health..."
for i in $(seq 1 60); do
  if docker exec "$LEGACY_CONTAINER" python - <<PY >/dev/null 2>&1
import requests, sys
try:
    r = requests.get("http://${V4_CONTAINER}:8000/api/v1/health", timeout=2)
    sys.exit(0 if r.status_code == 200 else 1)
except Exception:
    sys.exit(1)
PY
  then
    break
  fi
  sleep 2
done

if ! docker exec "$LEGACY_CONTAINER" python - <<PY >/dev/null 2>&1
import requests, sys
try:
    r = requests.get("http://${V4_CONTAINER}:8000/api/v1/health", timeout=5)
    sys.exit(0 if r.status_code == 200 else 1)
except Exception:
    sys.exit(1)
PY
then
  echo "ERROR: v4 health check failed (${V4_CONTAINER})" >&2
  exit 2
fi

if [[ ! -f "$CASES_FILE_HOST" ]]; then
  echo "[WARN] cases file not found on host: $CASES_FILE_HOST"
  echo "[WARN] comparator will fail if no fallback cases are provided."
fi

docker cp scripts/rag_orchestrator_compare.py "$LEGACY_CONTAINER:/tmp/rag_orchestrator_compare.py"
if [[ -f "$CASES_FILE_HOST" ]]; then
  docker cp "$CASES_FILE_HOST" "$LEGACY_CONTAINER:/tmp/rag_eval.yaml"
fi

if [[ -z "$API_PREFIX" ]]; then
  API_PREFIX="$(docker exec "$LEGACY_CONTAINER" sh -lc 'printf "%s" "${BACKEND_API_PREFIX:-/api/v1}"')"
fi
if [[ -z "$API_PREFIX" ]]; then
  API_PREFIX="/api/v1"
fi

echo "[INFO] running comparator..."
CMD=(
  python /tmp/rag_orchestrator_compare.py
  --legacy-base-url http://127.0.0.1:8000
  --v4-base-url "http://${V4_CONTAINER}:8000"
  --api-prefix "$API_PREFIX"
  --cases-file /tmp/rag_eval.yaml
  --json-out "$REPORT_PATH_CONTAINER"
  --max-source-hit-drop "$MAX_DROP"
)
if [[ -n "$KB_ID" ]]; then
  CMD+=(--kb-id "$KB_ID")
fi
if [[ "$PRINT_JSON" == "true" ]]; then
  CMD+=(--print-json)
fi

docker exec "$LEGACY_CONTAINER" "${CMD[@]}"

echo "[INFO] done"
echo "[INFO] report in container: $REPORT_PATH_CONTAINER"
if [[ -n "$HOST_DATA_DIR" ]]; then
  report_name="$(basename "$REPORT_PATH_CONTAINER")"
  echo "[INFO] report on host (mounted): ${HOST_DATA_DIR}/${report_name}"
fi

