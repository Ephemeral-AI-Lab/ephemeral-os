"""Root-cause probe for the overlay depth-16 cliff.

Reuses an existing Daytona sandbox (does not create or delete one).
Answers:
  1. Kernel + overlay-module info (one-shot diagnostic)
  2. Cliff bisection at fine resolution: depths {14,15,16,17,18,20,32}
  3. E1.4: does root-in-container (sudo, no userns) lift the cap?
  4. E1.3: at depth 16, retry with each of {metacopy=off, redirect_dir=off,
     index=off, nfs_export=off, volatile, xino=off}
  5. errno capture via strace -e mount (dmesg is blocked in container)
  6. lowerdir filesystem variants at depth 16: tmpfs vs rootfs

Usage:
    python -m backend.scripts.probe_overlay_depth_rootcause <sandbox_id>
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "backend" / "src"))
sys.path.insert(0, str(_ROOT / "backend" / "tests"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_ROOT / ".env")

_SETTINGS_PATH = Path.home() / ".ephemeralos" / "settings.json"
if _SETTINGS_PATH.exists():
    _settings = json.loads(_SETTINGS_PATH.read_text())
    for key in ("daytona_api_key", "daytona_api_url", "daytona_target"):
        env_key = key.upper()
        if not os.environ.get(env_key) and _settings.get(key):
            os.environ[env_key] = _settings[key]


PROBE = r"""
set +e

echo "============================================================"
echo "## 1. Kernel + overlay module info"
echo "============================================================"
echo "uname -r: $(uname -r)"
echo "uname -a: $(uname -a)"
echo "--- /proc/version ---"
cat /proc/version
echo "--- /proc/filesystems (overlay) ---"
grep overlay /proc/filesystems
echo "--- overlay module params ---"
if [ -d /sys/module/overlay/parameters ]; then
    for f in /sys/module/overlay/parameters/*; do
        echo "  $(basename $f) = $(cat $f 2>/dev/null)"
    done
else
    echo "  /sys/module/overlay/parameters MISSING (built-in, no sysfs knobs)"
fi
echo "--- /proc/sys/user limits ---"
for f in max_user_namespaces max_mnt_namespaces; do
    v=$(cat /proc/sys/user/$f 2>/dev/null)
    echo "  $f = $v"
done
echo "--- /proc/sys/fs limits ---"
for f in file-max nr_open; do
    v=$(cat /proc/sys/fs/$f 2>/dev/null)
    echo "  $f = $v"
done
echo "--- mount table (root + tmpfs) ---"
mount | head -20
echo "--- whoami / caps ---"
id
grep CapEff /proc/self/status
echo "--- sudo availability ---"
sudo -n true 2>&1 && echo "SUDO: YES" || echo "SUDO: NO"
echo

echo "============================================================"
echo "## 2. Cliff bisection at fine resolution (rootless userns)"
echo "============================================================"
ROOT=/dev/shm/eos-rc
rm -rf $ROOT && mkdir -p $ROOT
# build N layer dirs once
for i in $(seq 0 31); do
    L=$ROOT/L$(printf '%05d' $i)
    mkdir -p $L
    echo "layer-$i" > $L/marker.txt
done

probe_depth_rootless() {
    local depth=$1
    local extra_opts=$2
    local label=$3
    local UP=$ROOT/up_${depth}_$$
    local WK=$ROOT/wk_${depth}_$$
    local MG=$ROOT/mg_${depth}_$$
    rm -rf $UP $WK $MG
    mkdir -p $UP $WK $MG
    # build colon-joined lowerdir from layer (depth-1) down to 0 (newest-first)
    local LOWER=""
    for i in $(seq $((depth-1)) -1 0); do
        if [ -z "$LOWER" ]; then LOWER=$ROOT/L$(printf '%05d' $i); else LOWER=$LOWER:$ROOT/L$(printf '%05d' $i); fi
    done
    local OPTS="lowerdir=$LOWER,upperdir=$UP,workdir=$WK,userxattr"
    if [ -n "$extra_opts" ]; then OPTS="$OPTS,$extra_opts"; fi
    local OUT
    OUT=$(unshare -Urm bash -c "mount -t overlay overlay -o '$OPTS' '$MG' 2>&1; echo RC=\$?; umount '$MG' 2>/dev/null" 2>&1)
    local RC=$(echo "$OUT" | grep -o 'RC=[0-9]*' | tail -1 | cut -d= -f2)
    local ERR=$(echo "$OUT" | grep -v '^RC=' | head -1)
    printf "  depth=%3d %-32s rc=%s  err=%s\n" "$depth" "$label" "$RC" "$ERR"
    rm -rf $UP $WK $MG
}

for d in 14 15 16 17 18 20 32; do
    probe_depth_rootless $d "" "rootless-baseline"
done
echo

echo "============================================================"
echo "## 3. E1.4 -- root-in-container (no userns) vs rootless userns"
echo "============================================================"
probe_depth_root_no_userns() {
    local depth=$1
    local UP=$ROOT/up_root_${depth}_$$
    local WK=$ROOT/wk_root_${depth}_$$
    local MG=$ROOT/mg_root_${depth}_$$
    rm -rf $UP $WK $MG
    mkdir -p $UP $WK $MG
    local LOWER=""
    for i in $(seq $((depth-1)) -1 0); do
        if [ -z "$LOWER" ]; then LOWER=$ROOT/L$(printf '%05d' $i); else LOWER=$LOWER:$ROOT/L$(printf '%05d' $i); fi
    done
    # NO userxattr (root path)
    local OPTS="lowerdir=$LOWER,upperdir=$UP,workdir=$WK"
    local OUT
    OUT=$(sudo -n mount -t overlay overlay -o "$OPTS" "$MG" 2>&1)
    local RC=$?
    sudo -n umount "$MG" 2>/dev/null
    printf "  depth=%3d sudo-no-userns                  rc=%s  err=%s\n" "$depth" "$RC" "$OUT"
    rm -rf $UP $WK $MG
}

for d in 15 16 17 20 32 50; do
    probe_depth_root_no_userns $d
done
echo

echo "============================================================"
echo "## 4. E1.3 -- mount option matrix at depth 16 (rootless)"
echo "============================================================"
for opt in "metacopy=off" "redirect_dir=off" "index=off" "nfs_export=off" "volatile" "xino=off" "metacopy=off,redirect_dir=off,index=off"; do
    probe_depth_rootless 16 "$opt" "rootless+$opt"
done
echo

echo "============================================================"
echo "## 5. strace -e mount at depth 16 (capture exact errno)"
echo "============================================================"
UP=$ROOT/up_strace; WK=$ROOT/wk_strace; MG=$ROOT/mg_strace
rm -rf $UP $WK $MG; mkdir -p $UP $WK $MG
LOWER=""
for i in $(seq 15 -1 0); do
    if [ -z "$LOWER" ]; then LOWER=$ROOT/L$(printf '%05d' $i); else LOWER=$LOWER:$ROOT/L$(printf '%05d' $i); fi
done
if command -v strace >/dev/null 2>&1; then
    unshare -Urm bash -c "strace -e mount mount -t overlay overlay -o 'lowerdir=$LOWER,upperdir=$UP,workdir=$WK,userxattr' '$MG' 2>&1; umount '$MG' 2>/dev/null" 2>&1 | tail -10
else
    echo "  strace: not installed"
    # fallback: write a tiny C program
fi
rm -rf $UP $WK $MG
echo

echo "============================================================"
echo "## 6. lowerdir filesystem variants at depth 16"
echo "============================================================"
# Try with lowerdirs on rootfs (overlay2) instead of /dev/shm (tmpfs)
RF_ROOT=/tmp/eos-rc-rootfs
rm -rf $RF_ROOT && mkdir -p $RF_ROOT
for i in $(seq 0 16); do
    mkdir -p $RF_ROOT/L$(printf '%05d' $i)
    echo "rootfs-$i" > $RF_ROOT/L$(printf '%05d' $i)/marker.txt
done

probe_depth_rootless_at() {
    local depth=$1
    local fs_root=$2
    local fs_label=$3
    local UP=/dev/shm/up_fs_${depth}_$$
    local WK=/dev/shm/wk_fs_${depth}_$$
    local MG=/dev/shm/mg_fs_${depth}_$$
    rm -rf $UP $WK $MG; mkdir -p $UP $WK $MG
    local LOWER=""
    for i in $(seq $((depth-1)) -1 0); do
        if [ -z "$LOWER" ]; then LOWER=$fs_root/L$(printf '%05d' $i); else LOWER=$LOWER:$fs_root/L$(printf '%05d' $i); fi
    done
    local OUT
    OUT=$(unshare -Urm bash -c "mount -t overlay overlay -o 'lowerdir=$LOWER,upperdir=$UP,workdir=$WK,userxattr' '$MG' 2>&1; echo RC=\$?; umount '$MG' 2>/dev/null" 2>&1)
    local RC=$(echo "$OUT" | grep -o 'RC=[0-9]*' | tail -1 | cut -d= -f2)
    printf "  depth=%3d lower=%-12s rc=%s\n" "$depth" "$fs_label" "$RC"
    rm -rf $UP $WK $MG
}

for d in 15 16 17 32; do
    probe_depth_rootless_at $d $RF_ROOT "rootfs(overlay2)"
done
rm -rf $RF_ROOT
echo

echo "============================================================"
echo "## 7. Stack-depth sanity"
echo "============================================================"
# Confirm tmpfs s_stack_depth is 0 by checking what FILESYSTEM_MAX_STACK_DEPTH says.
# We can probe by trying overlay-on-overlay-on-tmpfs.
echo "  /dev/shm fs type: $(stat -f -c %T /dev/shm 2>/dev/null)"
echo "  / fs type: $(stat -f -c %T / 2>/dev/null)"
echo "  /tmp fs type: $(stat -f -c %T /tmp 2>/dev/null)"
echo

rm -rf $ROOT
echo "### DONE ###"
"""


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: probe_overlay_depth_rootcause.py <sandbox_id>", file=sys.stderr)
        return 2
    sandbox_id = sys.argv[1]

    if not os.environ.get("DAYTONA_API_KEY") or not os.environ.get("DAYTONA_API_URL"):
        print("ERROR: DAYTONA_API_KEY / DAYTONA_API_URL not set.", file=sys.stderr)
        return 2

    from sandbox.testing import get_sandbox_service
    from test_e2e.daytona_exec_io import write_text_via_exec

    svc = get_sandbox_service()
    sandbox = svc.get_sandbox_object(sandbox_id)
    print(f"[probe] reusing sandbox: {sandbox_id}", flush=True)

    write_text_via_exec(sandbox, "/tmp/overlay_rc_probe.sh", PROBE)
    resp = sandbox.process.exec("bash /tmp/overlay_rc_probe.sh", timeout=240)
    print(str(getattr(resp, "result", "") or ""))
    print(f"[probe] exit_code={getattr(resp, 'exit_code', None)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
