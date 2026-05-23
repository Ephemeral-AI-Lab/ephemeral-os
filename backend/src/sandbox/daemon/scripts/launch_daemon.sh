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

# A daemon process can be in state "Z (zombie)" when its parent dies before
# reaping it — common in containers whose PID 1 is `sleep infinity` (the
# default sweevo image init). `kill -0` returns success for zombies because
# the PID still exists in the process table, so the old liveness check
# silently treated a dead daemon as healthy and refused to respawn. Read
# /proc/<pid>/status and reject non-runnable states. Returns 0 only when
# the process actually exists AND is in a state that can serve requests
# (R/S/D/T/t — anything but Z, X, or missing).
daemon_pid_alive() {
    pid=$1
    [ -z "$pid" ] && return 1
    [ ! -d "/proc/$pid" ] && return 1
    state=$(awk '/^State:/ {print substr($2, 1, 1); exit}' "/proc/$pid/status" 2>/dev/null)
    case "$state" in
        R|S|D|T|t) return 0 ;;
        *) return 1 ;;
    esac
}

# Fast-path: if the daemon is already up with the correct env, do not enter
# the critical section at all. This avoids per-call flock contention when
# the daemon is healthy (the steady-state case).
if [ -S "$SOCK" ] && [ -f "$PID" ] && daemon_pid_alive "$(cat "$PID" 2>/dev/null)"; then
    if [ -f "$ENV_FILE" ] && [ "$(cat "$ENV_FILE")" = "$ENV_SIG" ]; then
        exit 0
    fi
fi

# Serialise the kill+respawn window so N parallel callers don't each delete
# the socket and race to bind it. Inside the critical section we re-check
# liveness because another caller may have spawned the daemon while we were
# waiting on the lock. We hold the lock for at most ~10s (the bind wait
# below); concurrent callers either fast-path out or block briefly.
LOCK_FILE="${SOCK}.launch.v2.lock"
if command -v flock >/dev/null 2>&1; then
    exec 9>"$LOCK_FILE"
    flock 9
fi

if [ -S "$SOCK" ] && [ -f "$PID" ] && daemon_pid_alive "$(cat "$PID" 2>/dev/null)"; then
    if [ -f "$ENV_FILE" ] && [ "$(cat "$ENV_FILE")" = "$ENV_SIG" ]; then
        exit 0
    fi
    kill "$(cat "$PID" 2>/dev/null)" 2>/dev/null || true
fi

# Reap any leftover zombie record we saw earlier. ``wait $pid`` only works
# when the caller is the child's parent; the daemon zombie's parent is the
# container's init (often ``sleep infinity``, which never reaps). Best
# effort: send SIGCHLD to PID 1 (no-op on most inits) then drop the stale
# PID file so the spawn below isn't confused by it.
OLD_PID=$(cat "$PID" 2>/dev/null || echo "")
if [ -n "$OLD_PID" ] && [ -d "/proc/$OLD_PID" ]; then
    rm -f "$PID"
fi

rm -f "$SOCK"

for py in $PYTHON_CANDIDATES; do
    if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
        nohup "$py" -m "$MODULE" --socket "$SOCK" --pid-file "$PID" 9>&- </dev/null >"$LOG" 2>&1 &
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
