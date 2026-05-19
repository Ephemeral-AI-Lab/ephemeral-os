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

# Fast-path: if the daemon is already up with the correct env, do not enter
# the critical section at all. This avoids per-call flock contention when
# the daemon is healthy (the steady-state case).
if [ -S "$SOCK" ] && [ -f "$PID" ] && kill -0 "$(cat "$PID" 2>/dev/null)" 2>/dev/null; then
    if [ -f "$ENV_FILE" ] && [ "$(cat "$ENV_FILE")" = "$ENV_SIG" ]; then
        exit 0
    fi
fi

# Serialise the kill+respawn window so N parallel callers don't each delete
# the socket and race to bind it. Inside the critical section we re-check
# liveness because another caller may have spawned the daemon while we were
# waiting on the lock. We hold the lock for at most ~10s (the bind wait
# below); concurrent callers either fast-path out or block briefly.
LOCK_FILE="${SOCK}.launch.lock"
if command -v flock >/dev/null 2>&1; then
    exec 9>"$LOCK_FILE"
    flock 9
fi

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
        for _ in $(seq 1 200); do
            [ -S "$SOCK" ] && exit 0
            sleep 0.05
        done
        echo 'sandbox daemon failed to bind socket within 10s' >&2
        exit 1
    fi
done

echo 'sandbox daemon requires Python >= 3.10' >&2
exit 127
