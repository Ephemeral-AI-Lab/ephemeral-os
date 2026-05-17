#!/usr/bin/env bash
# Daytona stack health probe + state-machine escape hatch.
#
# Implements §6 of progressive-live-test-tiers-design-20260508.md:
#   1. Probe Daytona /api/health.
#   2. If `docker` is available, inspect daytona-runner-1. The API can
#      be healthy while the runner is wedged before serving port 3003.
#   3. If the runner is wedged by a stale inner containerd PID file,
#      remove only that stale PID file and restart the runner container.
#   4. If `docker` is available, look for sandbox rows stuck in
#      state='starting' or state='pending_build' for >60s in the
#      daytona-db-1 Postgres container.
#   5. If stuck rows are found, force-flip them to state='destroyed'
#      via a direct SQL UPDATE that bypasses the broken state-machine
#      transition the API enforces.
#
# Stdout is structured into three labeled sections (HEALTH, STUCK_ROWS,
# RUNNER, RECOVERY) so the Python tier-0 probe can parse it. Exit codes:
#   0 = healthy, or stuck rows found AND recovery succeeded
#   1 = health endpoint failure
#   2 = recovery required but not possible (docker unavailable, runner
#       cleanup/restart failed, or recovery SQL failed)

set -euo pipefail

API_URL="${1:-http://localhost:3000/api}"

print_section() {
    printf '=== %s ===\n' "$1"
}

# --- HEALTH ---
print_section HEALTH

if ! command -v curl >/dev/null 2>&1; then
    printf 'health_probe_error=missing_curl\n'
    exit 1
fi

http_code=$(curl --connect-timeout 5 --max-time 10 -s -o /dev/null \
    -w '%{http_code}' "${API_URL}/health" 2>/dev/null || echo "000")

if [ "${http_code}" = "200" ]; then
    printf 'api_health=ok http_code=%s\n' "${http_code}"
    health_ok=1
else
    printf 'api_health=non_200 http_code=%s\n' "${http_code}"
    health_ok=0
fi

# --- STUCK_ROWS ---
print_section STUCK_ROWS

if ! command -v docker >/dev/null 2>&1; then
    printf 'docker_unavailable=true\n'
    docker_ok=0
    stuck_ids=""
else
    docker_ok=1
    # `docker exec` returns non-zero if the container is missing; capture
    # stderr so the operator can see *why* recovery isn't running.
    if ! stuck_ids=$(docker exec daytona-db-1 psql -U user -d daytona -t -A \
        -c "SELECT id FROM sandbox WHERE state IN ('starting', 'pending_build') AND \"updatedAt\" < NOW() - INTERVAL '60 seconds'" \
        2>&1); then
        printf 'db_probe_error=true\n'
        printf '%s\n' "${stuck_ids}" | sed 's/^/db_probe_stderr: /'
        stuck_ids=""
        docker_ok=0
    fi
fi

stuck_count=0
if [ -n "${stuck_ids}" ]; then
    # Strip blank lines; psql -t outputs one row per line.
    stuck_count=$(printf '%s\n' "${stuck_ids}" | grep -c -E '^.+$' || true)
fi
printf 'stuck_row_count=%s\n' "${stuck_count}"
if [ "${stuck_count}" -gt 0 ]; then
    printf '%s\n' "${stuck_ids}" | sed 's/^/stuck_row: /'
fi

# --- RUNNER ---
print_section RUNNER

runner_recovery_required=0
stale_containerd_pid=""

if [ "${docker_ok}" -eq 0 ]; then
    printf 'runner_probe_skipped=docker_unavailable\n'
else
    if ! runner_health=$(docker inspect daytona-runner-1 \
        --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
        2>&1); then
        printf 'runner_probe_error=true\n'
        printf '%s\n' "${runner_health}" | sed 's/^/runner_probe_stderr: /'
        runner_recovery_required=1
    else
        printf 'runner_health=%s\n' "${runner_health}"
        if [ "${runner_health}" != "healthy" ]; then
            if stale_containerd_pid=$(docker exec daytona-runner-1 sh -lc '
                pid_file=/run/docker/containerd/containerd.pid
                if [ -s "$pid_file" ]; then
                    pid="$(cat "$pid_file" 2>/dev/null || true)"
                    if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
                        printf "%s" "$pid"
                    fi
                fi
            ' 2>/dev/null); then
                if [ -n "${stale_containerd_pid}" ]; then
                    printf 'stale_containerd_pid=%s\n' "${stale_containerd_pid}"
                else
                    printf 'stale_containerd_pid=none\n'
                fi
            else
                printf 'stale_containerd_pid_probe_failed=true\n'
            fi
            runner_recovery_required=1
        fi
    fi
fi

# --- RECOVERY ---
print_section RECOVERY

if [ "${stuck_count}" -eq 0 ] && [ "${runner_recovery_required}" -eq 0 ]; then
    printf 'recovery_required=false\n'
    if [ "${health_ok}" -eq 1 ]; then
        exit 0
    fi
    exit 1
fi

if [ "${docker_ok}" -eq 0 ]; then
    printf 'recovery_attempted=false reason=docker_unavailable\n'
    exit 2
fi

if [ "${runner_recovery_required}" -eq 1 ]; then
    if [ -z "${stale_containerd_pid}" ]; then
        printf 'runner_recovery_attempted=false reason=no_stale_containerd_pid\n'
        exit 2
    fi
    printf 'runner_recovery_attempted=true stale_containerd_pid=%s\n' "${stale_containerd_pid}"
    if docker exec daytona-runner-1 sh -lc '
        set -eu
        pid_file=/run/docker/containerd/containerd.pid
        if [ ! -s "$pid_file" ]; then
            echo "stale_containerd_pid_missing"
            exit 1
        fi
        pid="$(cat "$pid_file" 2>/dev/null || true)"
        if [ -z "$pid" ]; then
            echo "stale_containerd_pid_empty"
            exit 1
        fi
        if kill -0 "$pid" 2>/dev/null; then
            echo "containerd_pid_alive=$pid"
            exit 1
        fi
        rm -f "$pid_file"
        echo "stale_containerd_pid_removed=$pid"
    ' && docker restart daytona-runner-1 >/dev/null; then
        printf 'runner_recovery_succeeded=true\n'
    else
        printf 'runner_recovery_succeeded=false\n'
        exit 2
    fi
fi

if [ "${stuck_count}" -eq 0 ]; then
    if [ "${health_ok}" -eq 1 ]; then
        exit 0
    fi
    exit 1
fi

printf 'recovery_attempted=true\n'
if docker exec daytona-db-1 psql -U user -d daytona \
    -c "UPDATE sandbox SET state='destroyed', \"desiredState\"='destroyed' WHERE state IN ('starting', 'pending_build') AND \"updatedAt\" < NOW() - INTERVAL '60 seconds'" \
    >/dev/null 2>&1; then
    printf 'recovery_succeeded=true rows=%s\n' "${stuck_count}"
    exit 0
fi

printf 'recovery_succeeded=false\n'
exit 2
