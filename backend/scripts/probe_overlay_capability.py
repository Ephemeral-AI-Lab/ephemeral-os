"""One-shot Daytona probe: what overlay mount modes are available?

Spins up one test sandbox, runs a diagnostic bash script covering:
  1. uid / caps
  2. rootfs + /tmp filesystem type
  3. reflink support (cp --reflink=always)
  4. privileged overlay mount (no userxattr, same-fs lowerdir)
  5. privileged overlay with cross-fs lowerdir (tmpfs upperdir + rootfs lowerdir)
  6. rootless overlay via unshare -Urm + userxattr (current baseline)

Prints raw output, deletes the sandbox, exits.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# repo_root/backend/scripts/probe_overlay_capability.py -> repo_root
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "backend" / "src"))

from dotenv import load_dotenv

load_dotenv(_ROOT / ".env")

# Load settings.json (same pattern as live tests).
import json
_SETTINGS_PATH = Path.home() / ".ephemeralos" / "settings.json"
if _SETTINGS_PATH.exists():
    _settings = json.loads(_SETTINGS_PATH.read_text())
    for key in ("daytona_api_key", "daytona_api_url", "daytona_target"):
        env_key = key.upper()
        if not os.environ.get(env_key) and _settings.get(key):
            os.environ[env_key] = _settings[key]


PROBE_SCRIPT = r"""
set +e
echo "### uid/caps ###"
id
echo
echo "--- /proc/self/status caps ---"
grep -E 'Cap(Inh|Prm|Eff|Bnd|Amb):' /proc/self/status
echo
if command -v capsh >/dev/null 2>&1; then
    echo "--- capsh decode (Eff) ---"
    CAPEFF=$(grep CapEff /proc/self/status | awk '{print $2}')
    capsh --decode=$CAPEFF 2>&1 | head -3
else
    echo "capsh: not installed"
fi
echo

echo "### fs types ###"
if command -v findmnt >/dev/null 2>&1; then
    findmnt -D / /tmp 2>&1 | head -5
else
    echo "findmnt missing; falling back to stat -f"
    stat -f -c 'mount=%n fstype=%T' / /tmp 2>&1
fi
echo

echo "### reflink support ###"
RW=$(mktemp -d)
echo "hello reflink" > "$RW/a"
if cp --reflink=always "$RW/a" "$RW/b" 2>&1; then
    echo "REFLINK: YES (rootfs)"
else
    echo "REFLINK: NO (rootfs)"
fi
rm -rf "$RW"
TR=$(mktemp -d -p /tmp)
echo "x" > "$TR/a"
if cp --reflink=always "$TR/a" "$TR/b" 2>/dev/null; then
    echo "REFLINK: YES (/tmp)"
else
    echo "REFLINK: NO (/tmp)"
fi
rm -rf "$TR"
echo

echo "### privileged overlay (no userxattr, same-fs) ###"
WK=$(mktemp -d)
mkdir -p "$WK/lo" "$WK/up" "$WK/wk" "$WK/mg"
echo base > "$WK/lo/a"
OUT=$(mount -t overlay overlay -o lowerdir="$WK/lo",upperdir="$WK/up",workdir="$WK/wk" "$WK/mg" 2>&1)
if [ $? -eq 0 ]; then
    echo "PRIV_OVERLAY_SAMEFS: YES"
    echo "modified" > "$WK/mg/a"
    echo "   upperdir now contains: $(cat $WK/up/a 2>/dev/null)"
    umount "$WK/mg" 2>/dev/null
else
    echo "PRIV_OVERLAY_SAMEFS: NO -- $OUT"
fi
rm -rf "$WK" 2>/dev/null
echo

echo "### privileged overlay with tmpfs upperdir + rootfs lowerdir (cross-fs) ###"
WK=$(mktemp -d -p /tmp)
mkdir -p "$WK/up" "$WK/wk" "$WK/mg"
# lowerdir = an existing rootfs dir (use /etc for certainty of content)
OUT=$(mount -t overlay overlay -o lowerdir=/etc,upperdir="$WK/up",workdir="$WK/wk" "$WK/mg" 2>&1)
if [ $? -eq 0 ]; then
    echo "PRIV_OVERLAY_CROSSFS: YES  (<-- this is the ideal path: no cp -a)"
    [ -f "$WK/mg/hostname" ] && echo "   /etc/hostname visible in merged view: OK"
    umount "$WK/mg" 2>/dev/null
else
    echo "PRIV_OVERLAY_CROSSFS: NO -- $OUT"
fi
rm -rf "$WK" 2>/dev/null
echo

echo "### rootless overlay via unshare -Urm + userxattr (current baseline) ###"
WK=$(mktemp -d)
unshare -Urm bash -c "
    set +e
    mkdir -p '$WK/ns'
    mount -t tmpfs -o size=32m tmpfs '$WK/ns' 2>&1 || { echo UNSHARE_TMPFS: NO; exit 1; }
    mkdir -p '$WK/ns/lo' '$WK/ns/up' '$WK/ns/wk' '$WK/ns/mg'
    echo base > '$WK/ns/lo/a'
    if mount -t overlay overlay -o lowerdir='$WK/ns/lo',upperdir='$WK/ns/up',workdir='$WK/ns/wk',userxattr '$WK/ns/mg' 2>&1; then
        echo 'USERXATTR_OVERLAY: YES'
    else
        echo 'USERXATTR_OVERLAY: NO'
    fi
    umount '$WK/ns' 2>/dev/null
"
rm -rf "$WK" 2>/dev/null
echo

echo "### unshare + cross-fs overlay (no userxattr, bind lowerdir from rootfs) ###"
# This probes whether, inside the userns, we can skip cp -a by using a
# bind-mount from rootfs as lowerdir.
WK=$(mktemp -d)
unshare -Urm bash -c "
    set +e
    mkdir -p '$WK/ns'
    mount -t tmpfs -o size=32m tmpfs '$WK/ns' 2>&1 || { echo CROSS_UNSHARE_TMPFS: NO; exit 1; }
    mkdir -p '$WK/ns/up' '$WK/ns/wk' '$WK/ns/mg' '$WK/ns/lo_bind'
    mount --bind /etc '$WK/ns/lo_bind' 2>&1 && echo BIND_IN_USERNS: YES || echo BIND_IN_USERNS: NO
    mount -t overlay overlay -o lowerdir='$WK/ns/lo_bind',upperdir='$WK/ns/up',workdir='$WK/ns/wk',userxattr '$WK/ns/mg' 2>&1 \
        && echo USERNS_CROSSFS_OVERLAY: YES \
        || echo USERNS_CROSSFS_OVERLAY: NO
    umount '$WK/ns/mg' 2>/dev/null
    umount '$WK/ns/lo_bind' 2>/dev/null
    umount '$WK/ns' 2>/dev/null
"
rm -rf "$WK" 2>/dev/null
echo

echo "### DONE ###"
"""


def main() -> int:
    if not os.environ.get("DAYTONA_API_KEY") or not os.environ.get("DAYTONA_API_URL"):
        print("ERROR: DAYTONA_API_KEY / DAYTONA_API_URL not set.", file=sys.stderr)
        return 2

    from sandbox.testing import create_test_sandbox, delete_test_sandbox, get_sandbox_service
    sys.path.insert(0, str(_ROOT / "backend" / "tests"))
    from test_e2e.daytona_exec_io import write_text_via_exec

    info = create_test_sandbox(name="overlay-capability-probe")
    sandbox_id = info["id"]
    print(f"[probe] sandbox id: {sandbox_id}", flush=True)
    try:
        svc = get_sandbox_service()
        sandbox = svc.get_sandbox_object(sandbox_id)
        write_text_via_exec(sandbox, "/tmp/overlay_probe.sh", PROBE_SCRIPT)
        resp = sandbox.process.exec("bash /tmp/overlay_probe.sh", timeout=90)
        print(str(getattr(resp, "result", "") or ""))
        print(f"[probe] main exit_code={getattr(resp, 'exit_code', None)}")

        ESC = r"""
echo "### sudo availability ###"
if command -v sudo >/dev/null 2>&1; then
    sudo -n true 2>&1 && echo SUDO_NO_PASSWD: YES || echo SUDO_NO_PASSWD: NO
else
    echo sudo: not installed
fi
echo
echo "### attempted privileged overlay via sudo ###"
WK=$(mktemp -d); mkdir -p $WK/lo $WK/up $WK/wk $WK/mg; echo base > $WK/lo/a
if sudo -n mount -t overlay overlay -o lowerdir=$WK/lo,upperdir=$WK/up,workdir=$WK/wk $WK/mg 2>&1; then
    echo SUDO_PRIV_OVERLAY: YES
    sudo -n umount $WK/mg 2>/dev/null
else
    echo SUDO_PRIV_OVERLAY: NO
fi
rm -rf $WK
"""
        write_text_via_exec(sandbox, "/tmp/overlay_esc.sh", ESC)
        resp2 = sandbox.process.exec("bash /tmp/overlay_esc.sh", timeout=30)
        print(str(getattr(resp2, "result", "") or ""))
    finally:
        try:
            delete_test_sandbox(sandbox_id)
            print(f"[probe] deleted sandbox {sandbox_id}", flush=True)
        except Exception as exc:
            print(f"[probe] WARNING: delete failed: {exc}", file=sys.stderr)
    return 0


def repr_bash(script: str) -> str:
    escaped = script.replace("'", "'\\''")
    return f"'{escaped}'"


if __name__ == "__main__":
    sys.exit(main())
