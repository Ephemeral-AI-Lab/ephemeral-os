"""Follow-up overlay probe: tests the narrower privileged paths we didn't rule out.

Covers:
  A. sudo mount -t tmpfs <fresh>, then sudo overlay (lowerdir=workspace, upperdir=fresh tmpfs)
  B. unshare -Urm with bind-mount of the actual workspace path (not /etc) as lowerdir
  C. filesystem inventory: workspace vs /tmp vs rootfs — are they the same fs?
  D. sudo bind-mount visibility

Preflight for overlay-sandbox-plan.md (kernel behaviors the plan asserts):
  D. MOUNT ORDERING: /ns/lower stays bound to the ORIGINAL tree after
     /ns/merged is bind-mounted over $WS (no recursive-overlay loop).
  E. WRITE-TO-LOWER-UNDER-OVERLAY: direct-merge writes to /ns/lower while
     the overlay is mounted land on the real disk and survive ns exit.
  F. USER-NS / PID-NS AT CONCURRENCY: 10 parallel `unshare -Urm` succeed;
     PID ns is NOT new (so ps / pytest-xdist still see host procs).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "backend" / "src"))
sys.path.insert(0, str(_ROOT / "backend" / "tests"))

from dotenv import load_dotenv
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

echo "### filesystem inventory ###"
for p in / /tmp /home /home/daytona $HOME; do
    if [ -e "$p" ]; then
        st=$(stat -f -c 'fstype=%T block_size=%s' "$p" 2>&1)
        echo "  $p -> $st"
    fi
done
echo
echo "--- /proc/mounts excerpt ---"
awk '$2 ~ /^(\/|\/tmp|\/home|\/workspace)$/ || $3 ~ /overlay|tmpfs/' /proc/mounts | head -20
echo
echo "--- workspace path ---"
echo "HOME=$HOME"
echo "PWD=$PWD"
ls -la $HOME | head -5
echo

echo "### A. sudo mount tmpfs + overlay with workspace lowerdir ###"
WK=$(mktemp -d)
sudo -n mount -t tmpfs -o size=64m tmpfs "$WK" 2>&1 && echo "  SUDO_TMPFS: YES" || { echo "  SUDO_TMPFS: NO"; rm -rf "$WK"; exit 0; }
mkdir -p "$WK/up" "$WK/wk" "$WK/mg"
LOWER="$HOME"   # live workspace (outside the fresh tmpfs)
OUT=$(sudo -n mount -t overlay overlay -o lowerdir="$LOWER",upperdir="$WK/up",workdir="$WK/wk" "$WK/mg" 2>&1)
if [ $? -eq 0 ]; then
    echo "  SUDO_OVERLAY_CROSSFS_LIVELOW: YES"
    echo "  sample: $(ls $WK/mg | head -3 | tr '\n' ' ')"
    # Write a file through the merged view, confirm upperdir captures
    echo probe > "$WK/mg/.overlay_probe_marker" 2>&1
    [ -f "$WK/up/.overlay_probe_marker" ] && echo "  UPPERDIR_CAPTURES_WRITE: YES" || echo "  UPPERDIR_CAPTURES_WRITE: NO"
    # Confirm live workspace unaffected
    [ -f "$HOME/.overlay_probe_marker" ] && echo "  LIVE_LEAK: YES (BAD)" || echo "  LIVE_LEAK: NO (good, writes isolated)"
    sudo -n umount "$WK/mg" 2>/dev/null
else
    echo "  SUDO_OVERLAY_CROSSFS_LIVELOW: NO -- $OUT"
fi
sudo -n umount "$WK" 2>/dev/null
rm -rf "$WK" 2>/dev/null
echo

echo "### B. unshare + bind-mount of \$HOME as lowerdir ###"
WK=$(mktemp -d)
unshare -Urm bash -c "
    set +e
    mkdir -p '$WK/ns'
    mount -t tmpfs -o size=32m tmpfs '$WK/ns' 2>&1 || { echo '  USERNS_TMPFS: NO'; exit 1; }
    mkdir -p '$WK/ns/lo' '$WK/ns/up' '$WK/ns/wk' '$WK/ns/mg'
    if mount --bind '$HOME' '$WK/ns/lo' 2>&1; then
        echo '  USERNS_BIND_WORKSPACE: YES'
    else
        echo '  USERNS_BIND_WORKSPACE: NO'
        exit 1
    fi
    if mount -t overlay overlay -o lowerdir='$WK/ns/lo',upperdir='$WK/ns/up',workdir='$WK/ns/wk',userxattr '$WK/ns/mg' 2>&1; then
        echo '  USERNS_CROSSFS_BIND_OVERLAY: YES'
        echo '  sample: '\$(ls '$WK/ns/mg' | head -3 | tr '\\n' ' ')
        echo 'probe' > '$WK/ns/mg/.overlay_probe_marker' 2>&1
        [ -f '$WK/ns/up/.overlay_probe_marker' ] && echo '  USERNS_UPPER_CAPTURES: YES' || echo '  USERNS_UPPER_CAPTURES: NO'
    else
        echo '  USERNS_CROSSFS_BIND_OVERLAY: NO'
    fi
    umount '$WK/ns/mg' 2>/dev/null
    umount '$WK/ns/lo' 2>/dev/null
    umount '$WK/ns' 2>/dev/null
"
rm -rf "$WK" 2>/dev/null
echo

echo "### C. cp -a benchmark on \$HOME (baseline materialize tax) ###"
# Measure one cp -a cycle so we know what the current cost is per op.
du -sh "$HOME" 2>/dev/null | head -1
SINK=$(mktemp -d)
time cp -a "$HOME"/. "$SINK/" 2>&1 | tail -3
rm -rf "$SINK"
echo

echo "### D. mount-ordering: lower identity after merged rebind ###"
# NOTE: overlayfs requires upperdir + workdir on the SAME filesystem. Plan §3.1's
# "two separate tmpfs" recipe is broken — kernel rejects with EINVAL. We use one
# tmpfs at \$WK/ns (matches test B's working pattern) and put upper/work as subdirs.
WS=$(mktemp -d)
echo "tracked v1" > "$WS/tracked.txt"
WS_INODE_BEFORE=$(stat -c '%i' "$WS/tracked.txt")
echo "  pre-rebind \$WS/tracked.txt inode=$WS_INODE_BEFORE"
WK=$(mktemp -d)
unshare -Urm bash -c "
    set +e
    mkdir -p '$WK/ns'
    mount -t tmpfs -o size=64m tmpfs '$WK/ns' || { echo '  D_TMPFS_NS: FAIL'; exit 1; }
    mkdir -p '$WK/ns/lower' '$WK/ns/upper' '$WK/ns/work' '$WK/ns/merged'
    mount --bind '$WS' '$WK/ns/lower' && echo '  D_BIND_LOWER: OK' || { echo '  D_BIND_LOWER: FAIL'; exit 1; }
    mount -t overlay overlay \
        -o lowerdir='$WK/ns/lower',upperdir='$WK/ns/upper',workdir='$WK/ns/work',userxattr \
        '$WK/ns/merged' && echo '  D_OVERLAY_MOUNT: OK' || { echo '  D_OVERLAY_MOUNT: FAIL'; exit 1; }
    # Plan's final step: bind merged on top of the original WS path.
    mount --bind '$WK/ns/merged' '$WS' && echo '  D_REBIND_MERGED_ON_WS: OK' || { echo '  D_REBIND_MERGED_ON_WS: FAIL'; exit 1; }

    LOWER_INODE=\$(stat -c '%i' '$WK/ns/lower/tracked.txt' 2>/dev/null)
    echo \"  D_LOWER_INODE_AFTER_REBIND=\$LOWER_INODE (expect $WS_INODE_BEFORE)\"
    if [ \"\$LOWER_INODE\" = \"$WS_INODE_BEFORE\" ]; then
        echo '  D_LOWER_STILL_ORIGINAL: YES'
    else
        echo '  D_LOWER_STILL_ORIGINAL: NO (recursive-overlay loop risk)'
    fi

    echo 'from-merged' > '$WK/ns/merged/new_upper.txt'
    [ -f '$WK/ns/upper/new_upper.txt' ] && echo '  D_WRITE_LANDS_IN_UPPER: YES' || echo '  D_WRITE_LANDS_IN_UPPER: NO'
    [ -f '$WK/ns/lower/new_upper.txt' ] && echo '  D_UPPER_LEAKED_TO_LOWER: YES (bad)' || echo '  D_UPPER_LEAKED_TO_LOWER: NO (good)'
    [ -f '$WS/new_upper.txt' ] && echo '  D_WS_SEES_MERGED: YES' || echo '  D_WS_SEES_MERGED: NO'

    umount '$WS' 2>/dev/null
    umount '$WK/ns/merged' 2>/dev/null
    umount '$WK/ns/lower' 2>/dev/null
    umount '$WK/ns' 2>/dev/null
"
[ -f "$WS/new_upper.txt" ] && echo "  D_POSTEXIT_UPPER_LEAKED: YES (BAD)" || echo "  D_POSTEXIT_UPPER_LEAKED: NO (good)"
rm -rf "$WK" "$WS"
echo

echo "### E. write-to-lower-under-overlay (direct-merge semantics) ###"
WS=$(mktemp -d)
echo "base v1" > "$WS/tracked.txt"
WK=$(mktemp -d)
unshare -Urm bash -c "
    set +e
    mkdir -p '$WK/ns'
    mount -t tmpfs -o size=64m tmpfs '$WK/ns' || exit 1
    mkdir -p '$WK/ns/lower' '$WK/ns/upper' '$WK/ns/work' '$WK/ns/merged'
    mount --bind '$WS' '$WK/ns/lower' || exit 1
    mount -t overlay overlay \
        -o lowerdir='$WK/ns/lower',upperdir='$WK/ns/upper',workdir='$WK/ns/work',userxattr \
        '$WK/ns/merged' || exit 1

    # E1: create a NEW file via /ns/lower (gitignored direct-merge case).
    echo 'direct-create' > '$WK/ns/lower/direct_add.txt' 2>&1 && echo '  E1_LOWER_WRITE_OK: YES' || echo '  E1_LOWER_WRITE_OK: NO'
    if [ -f '$WK/ns/merged/direct_add.txt' ]; then
        echo '  E1_MERGED_SEES_LOWER_ADD: YES'
    else
        echo '  E1_MERGED_SEES_LOWER_ADD: NO (kernel caches dir listing)'
    fi

    # E2: overwrite existing file via /ns/lower; merged may see old or new.
    echo 'lower-modified' > '$WK/ns/lower/tracked.txt'
    echo \"  E2_MERGED_AFTER_LOWER_MODIFY=\$(cat $WK/ns/merged/tracked.txt 2>/dev/null)\"

    # E3: upper+lower both write same path — upper must win in merged.
    echo 'upper-wins' > '$WK/ns/merged/tracked.txt'
    echo 'lower-loses' > '$WK/ns/lower/tracked.txt'
    echo \"  E3_MERGED_AFTER_BOTH=\$(cat $WK/ns/merged/tracked.txt 2>/dev/null) (expect upper-wins)\"
    echo \"  E3_UPPER_HAS_COPY=\$(cat $WK/ns/upper/tracked.txt 2>/dev/null)\"

    dmesg 2>/dev/null | tail -5 | grep -iE 'overlay|EIO|bug' && echo '  E4_DMESG_WARNINGS: see above' || echo '  E4_DMESG_WARNINGS: none (or blocked)'

    umount '$WK/ns/merged' 2>/dev/null
    umount '$WK/ns/lower' 2>/dev/null
    umount '$WK/ns' 2>/dev/null
"
if [ -f "$WS/direct_add.txt" ]; then
    echo "  E_POSTEXIT_DIRECT_ADD_PERSISTED: YES"
    echo "     content=$(cat $WS/direct_add.txt)"
else
    echo "  E_POSTEXIT_DIRECT_ADD_PERSISTED: NO (direct-merge path BROKEN)"
fi
echo "  E_POSTEXIT_TRACKED_CONTENT=$(cat $WS/tracked.txt 2>/dev/null) (expect 'lower-loses')"
rm -rf "$WK" "$WS"
echo

echo "### F. user-ns / PID-ns at concurrency (N=10) ###"
echo "  max_user_namespaces=$(cat /proc/sys/user/max_user_namespaces 2>/dev/null)"
echo "  max_mnt_namespaces=$(cat /proc/sys/user/max_mnt_namespaces 2>/dev/null)"
HOST_PID_NS=$(readlink /proc/self/ns/pid 2>/dev/null)
HOST_MNT_NS=$(readlink /proc/self/ns/mnt 2>/dev/null)
HOST_USER_NS=$(readlink /proc/self/ns/user 2>/dev/null)
echo "  host pid=$HOST_PID_NS  mnt=$HOST_MNT_NS  user=$HOST_USER_NS"
unshare -Urm bash -c "
    P=\$(readlink /proc/self/ns/pid)
    M=\$(readlink /proc/self/ns/mnt)
    U=\$(readlink /proc/self/ns/user)
    echo \"  inside pid=\$P  mnt=\$M  user=\$U\"
    [ \"\$P\" = \"$HOST_PID_NS\" ] && echo '  F_PID_NS_UNCHANGED: YES (ps/pytest-xdist safe)' || echo '  F_PID_NS_UNCHANGED: NO (unexpected)'
    [ \"\$M\" != \"$HOST_MNT_NS\" ] && echo '  F_MNT_NS_CHANGED: YES (expected)' || echo '  F_MNT_NS_CHANGED: NO (unexpected)'
    echo \"  F_PS_LINE_COUNT_INSIDE=\$(ps -e --no-headers 2>/dev/null | wc -l)\"
"
echo "--- F concurrent (10x unshare -Urm + overlay, 2s hold each) ---"
# Shared lower source across all 10 ops: a throwaway dir with some content.
F_LOWER_SRC=$(mktemp -d)
echo "shared-lower" > "$F_LOWER_SRC/marker.txt"
START=$(date +%s%N)
for i in $(seq 1 10); do
    (
        WK=$(mktemp -d)
        unshare -Urm bash -c "
            set +e
            mkdir -p '$WK/ns'
            mount -t tmpfs -o size=16m tmpfs '$WK/ns' 2>/dev/null || { echo \"  op$i: TMPFS_NS FAIL\"; exit 1; }
            mkdir -p '$WK/ns/lower' '$WK/ns/upper' '$WK/ns/work' '$WK/ns/merged'
            mount --bind '$F_LOWER_SRC' '$WK/ns/lower' 2>/dev/null || { echo \"  op$i: BIND FAIL\"; exit 1; }
            mount -t overlay overlay \
                -o lowerdir='$WK/ns/lower',upperdir='$WK/ns/upper',workdir='$WK/ns/work',userxattr \
                '$WK/ns/merged' 2>/dev/null || { echo \"  op$i: OVERLAY FAIL\"; exit 1; }
            sleep 2
            umount '$WK/ns/merged' 2>/dev/null
            umount '$WK/ns/lower' 2>/dev/null
            umount '$WK/ns' 2>/dev/null
            echo \"  op$i: OK\"
        "
        rm -rf "$WK" 2>/dev/null
    ) &
done
wait
END=$(date +%s%N)
echo "  F_CONCURRENT_WALL_MS=$(( (END - START) / 1000000 )) (expect ~2000-3000 if parallel)"
rm -rf "$F_LOWER_SRC"
echo

echo "### DONE ###"
"""


def main() -> int:
    if not os.environ.get("DAYTONA_API_KEY") or not os.environ.get("DAYTONA_API_URL"):
        print("ERROR: DAYTONA_API_KEY / DAYTONA_API_URL not set.", file=sys.stderr)
        return 2

    from sandbox.testing import create_test_sandbox, delete_test_sandbox, get_sandbox_service
    from test_e2e.daytona_exec_io import write_text_via_exec

    info = create_test_sandbox(name="overlay-followup-probe")
    sandbox_id = info["id"]
    print(f"[probe] sandbox id: {sandbox_id}", flush=True)
    try:
        svc = get_sandbox_service()
        sandbox = svc.get_sandbox_object(sandbox_id)
        write_text_via_exec(sandbox, "/tmp/overlay_followup.sh", PROBE)
        resp = sandbox.process.exec("bash /tmp/overlay_followup.sh", timeout=180)
        print(str(getattr(resp, "result", "") or ""))
        print(f"[probe] exit_code={getattr(resp, 'exit_code', None)}")
    finally:
        try:
            delete_test_sandbox(sandbox_id)
            print(f"[probe] deleted sandbox {sandbox_id}", flush=True)
        except Exception as exc:
            print(f"[probe] WARNING: delete failed: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
