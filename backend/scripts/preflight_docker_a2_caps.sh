#!/usr/bin/env bash
# Preflight CI experiment for PLAN_v4 §6 Step 0.
#
# Empirically verifies that the default cap set
# (--cap-add=SYS_ADMIN --cap-add=NET_ADMIN
#  --security-opt seccomp=unconfined --security-opt apparmor=unconfined)
# is sufficient for:
#   1. `unshare -Urm true` inside the container,
#   2. `detect_private_mount_namespace()` returning True,
#   3. a single-lowerdir overlay mount + umount inside `unshare -Urm`,
#   4. isolated_workspace bridge create/delete via `ip link` (CAP_NET_ADMIN),
#   5. isolated_workspace nftables table create/delete (CAP_NET_ADMIN).
#
# Requires a Linux host with a local docker daemon. Bails out cleanly on
# non-Linux so this is safe to invoke from any CI matrix entry.

set -euo pipefail

if [ "$(uname -s)" != "Linux" ]; then
    echo "preflight_docker_a2_caps: non-Linux host ($(uname -s)); skipping." >&2
    exit 0
fi

if ! command -v docker >/dev/null 2>&1; then
    echo "preflight_docker_a2_caps: docker not on PATH; cannot run preflight." >&2
    exit 2
fi

IMAGE="${PREFLIGHT_IMAGE:-ubuntu:22.04}"
LOG_DIR="${PREFLIGHT_LOG_DIR:-.planning/ralplan-docker-provider/preflight-logs}"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/preflight_docker_a2_caps.log"

echo "preflight: pulling $IMAGE" | tee "$LOG_FILE"
docker pull "$IMAGE" >>"$LOG_FILE" 2>&1

PROBE_SCRIPT=$(cat <<'PROBE'
set -eu

echo "[probe 1/5] unshare -Urm true"
unshare -Urm true

echo "[probe 2/5] private mount namespace detection"
unshare -Urm bash -c 'mount --make-rprivate / 2>/dev/null && echo OK_PRIVATE_NAMESPACE'

echo "[probe 3/5] single-lowerdir overlay mount"
TMP=$(mktemp -d)
mkdir -p "$TMP/lower" "$TMP/upper" "$TMP/work" "$TMP/merged"
echo marker > "$TMP/lower/file.txt"

unshare -Urm bash -c "
    mount -t overlay overlay -o lowerdir=$TMP/lower,upperdir=$TMP/upper,workdir=$TMP/work $TMP/merged
    cat $TMP/merged/file.txt | grep -q marker
    umount $TMP/merged
"
rm -rf "$TMP"

echo "[probe 4/5] iws bridge create/delete (CAP_NET_ADMIN)"
ip link add eos-preflight-bridge type bridge
ip link del eos-preflight-bridge

echo "[probe 5/5] iws nftables table create/delete"
if command -v nft >/dev/null 2>&1; then
    nft add table inet eos_preflight
    nft delete table inet eos_preflight
else
    echo "  nft missing — skipping (install nftables in the runtime image)"
fi

echo "[probes complete] OK"
PROBE
)

set +e
docker run --rm \
    --cap-add=SYS_ADMIN \
    --cap-add=NET_ADMIN \
    --security-opt seccomp=unconfined \
    --security-opt apparmor=unconfined \
    "$IMAGE" \
    bash -c "$PROBE_SCRIPT" 2>&1 | tee -a "$LOG_FILE"
rc=${PIPESTATUS[0]}
set -e

if [ "$rc" -ne 0 ]; then
    echo "preflight: FAILED rc=$rc; see $LOG_FILE" >&2
    echo "preflight: A.2 insufficient — halt plan and re-trigger consensus per PLAN_v4 §6 Step 0." >&2
    exit "$rc"
fi

echo "preflight: PASS — Option A.2 sufficient on this Linux host"
echo "preflight: log $LOG_FILE"
