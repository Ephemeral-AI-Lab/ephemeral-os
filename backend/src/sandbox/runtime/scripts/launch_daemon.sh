#!/bin/sh
set -eu

if [ "$#" -ne 7 ]; then
    echo "usage: launch_daemon.sh <python-candidates> <socket> <pid> <log> <env-file> <env-signature> <module>" >&2
    exit 2
fi

PYTHON_CANDIDATES=$1
SOCK=$2
PID=$3
LOG=$4
ENV_FILE=$5
ENV_SIG=$6
MODULE=$7

mkdir -p "$(dirname "$SOCK")"

if [ -S "$SOCK" ] && [ -f "$PID" ] && kill -0 "$(cat "$PID" 2>/dev/null)" 2>/dev/null; then
    if [ -f "$ENV_FILE" ] && [ "$(cat "$ENV_FILE")" = "$ENV_SIG" ]; then
        exit 0
    fi
    kill "$(cat "$PID" 2>/dev/null)" 2>/dev/null || true
fi

rm -f "$SOCK"

for py in $PYTHON_CANDIDATES; do
    if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
        nohup "$py" -m "$MODULE" --socket "$SOCK" --pid-file "$PID" </dev/null >"$LOG" 2>&1 &
        printf '%s' "$ENV_SIG" > "$ENV_FILE"
        for _ in $(seq 1 50); do
            [ -S "$SOCK" ] && exit 0
            sleep 0.05
        done
        echo 'sandbox daemon failed to bind socket within 2.5s' >&2
        exit 1
    fi
done

echo 'sandbox daemon requires Python >= 3.10' >&2
exit 127
