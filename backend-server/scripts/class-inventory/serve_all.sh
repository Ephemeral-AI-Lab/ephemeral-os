#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

BIND="${CLASS_INVENTORY_ALL_BIND:-${CLASS_INVENTORY_BIND:-127.0.0.1}}"
PORT="${CLASS_INVENTORY_ALL_PORT:-${CLASS_INVENTORY_PORT:-8790}}"
BROWSER_APP="${CLASS_INVENTORY_BROWSER:-Google Chrome}"
PAGE="${1:-${CLASS_INVENTORY_ALL_PAGE:-}}"
BASE_URL="http://${BIND}:${PORT}"
LOG_FILE="${CLASS_INVENTORY_ALL_LOG:-${TMPDIR:-/tmp}/eos-class-inventory-all-${PORT}.log}"

usage() {
  cat <<'EOF'
Usage:
  backend-server/scripts/class-inventory/serve_all.sh [page]

Starts the aggregate class-inventory server if needed and opens it in Chrome.

Arguments:
  page    Optional aggregate page path, such as agent-core/index.html,
          sandbox/index.html, or backend-server/index.html. Defaults to /.

Environment:
  CLASS_INVENTORY_ALL_BIND     Bind address, default 127.0.0.1.
  CLASS_INVENTORY_ALL_PORT     Port, default 8790.
  CLASS_INVENTORY_ALL_PAGE     Page to open when no argument is provided.
  CLASS_INVENTORY_ALL_LOG      Server log path.
  CLASS_INVENTORY_BROWSER      macOS browser app, default Google Chrome.
EOF
}

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required command: $1" >&2
    exit 1
  fi
}

case "$PAGE" in
  -h | --help)
    usage
    exit 0
    ;;
  http://* | https://*)
    echo "page must be a path on $BASE_URL, not a full URL: $PAGE" >&2
    exit 1
    ;;
esac

need curl
need python3

server_ready() {
  curl -fsS "${BASE_URL}/__class_inventory_health" 2>/dev/null | grep -q '"aggregate-class-inventory"'
}

stale_class_inventory_server_ready() {
  curl -fsS "${BASE_URL}/" 2>/dev/null | grep -q "Class Inventories"
}

stop_stale_server() {
  if ! command -v lsof >/dev/null 2>&1; then
    echo "server at $BASE_URL looks stale; stop it manually or choose another port" >&2
    exit 1
  fi
  local pids
  pids="$(lsof -ti "tcp:${PORT}" || true)"
  if [[ -z "$pids" ]]; then
    return
  fi
  echo "stopping stale aggregate class-inventory server on $BASE_URL"
  kill $pids
  for _ in {1..40}; do
    if [[ -z "$(lsof -ti "tcp:${PORT}" || true)" ]]; then
      return
    fi
    sleep 0.25
  done
  echo "stale aggregate class-inventory server did not stop; pids: $pids" >&2
  exit 1
}

start_server() {
  mkdir -p "$(dirname -- "$LOG_FILE")"
  nohup python3 "$SCRIPT_DIR/serve_all.py" --bind "$BIND" --port "$PORT" >"$LOG_FILE" 2>&1 &
  local pid="$!"
  for _ in {1..40}; do
    if server_ready; then
      echo "started aggregate class-inventory server pid=$pid log=$LOG_FILE"
      return
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "aggregate class-inventory server failed to start; log follows:" >&2
      tail -n 60 "$LOG_FILE" >&2 || true
      exit 1
    fi
    sleep 0.25
  done
  echo "aggregate class-inventory server did not become ready; log follows:" >&2
  tail -n 60 "$LOG_FILE" >&2 || true
  exit 1
}

if server_ready; then
  echo "reusing aggregate class-inventory server at $BASE_URL"
else
  if stale_class_inventory_server_ready; then
    stop_stale_server
  fi
  start_server
fi

if [[ -n "$PAGE" ]]; then
  PAGE="${PAGE#/}"
  url="${BASE_URL}/${PAGE}"
else
  url="${BASE_URL}/"
fi

if command -v open >/dev/null 2>&1; then
  if ! open -a "$BROWSER_APP" "$url" 2>/dev/null; then
    open "$url"
  fi
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$url" >/dev/null 2>&1 &
else
  echo "open this URL: $url"
  exit 0
fi

echo "opened $url"
