"""Probe Daytona sandbox for per-run overlay capability.

Writes the probe as a file in the sandbox (avoiding any quoting hazards) then
runs it. Produces a verdict over which `OverlayAuditor` branch is reachable.
"""

from __future__ import annotations

import argparse
import base64
import sys
import textwrap
from pathlib import Path

BACKEND_SRC = Path(__file__).resolve().parents[1] / "backend" / "src"
sys.path.insert(0, str(BACKEND_SRC))

from sandbox.testing import create_test_sandbox, delete_test_sandbox  # noqa: E402
from sandbox.service import SandboxService  # noqa: E402

PROBE = r"""#!/bin/bash
set +e
echo "=== os ==="
uname -a
cat /etc/os-release 2>/dev/null | head -5
echo "=== mounts ==="
findmnt -T /tmp -n -o TARGET,SOURCE,FSTYPE,OPTIONS 2>/dev/null || mount | grep -E "on /tmp |on / "
findmnt -T / -n -o TARGET,SOURCE,FSTYPE,OPTIONS 2>/dev/null || true
echo "=== caps ==="
grep -E "CapEff|CapBnd|NoNewPrivs|Seccomp" /proc/self/status
echo "=== userns sysctls ==="
for f in /proc/sys/kernel/unprivileged_userns_clone /proc/sys/user/max_user_namespaces /proc/sys/user/max_mnt_namespaces; do
  printf "%s=" "$f"; cat "$f" 2>/dev/null || echo "missing"
done
echo "=== /dev/fuse ==="
ls -l /dev/fuse 2>&1 | head -1
echo "=== fuse-overlayfs presence ==="
command -v fuse-overlayfs 2>&1 || echo "missing"

# ---------- 1. kernel overlay (requires CAP_SYS_ADMIN in init ns) ----------
W1=$(mktemp -d)
cd "$W1" && mkdir lo up wk mg && echo base > lo/a
mount -t overlay overlay -o lowerdir=lo,upperdir=up,workdir=wk mg 2>&1
echo "kernel_overlay_rc=$?"
umount mg 2>/dev/null

# ---------- 2. unshare -m only (needs CAP_SYS_ADMIN too) ----------
unshare -m -- bash -c 'echo unshare_m_ok' 2>&1
echo "unshare_m_rc=$?"

# ---------- 3. unshare -Urm (unprivileged user+mount ns) ----------
unshare -Urm -- bash -c 'echo unshare_urm_ok; id' 2>&1
echo "unshare_urm_rc=$?"

# ---------- 4. overlay inside unshare -Urm ----------
W2=$(mktemp -d)
cd "$W2" && mkdir lo up wk mg && echo base > lo/a
cat > run.sh <<'INNER'
#!/bin/bash
set +e
mount -t overlay overlay -o lowerdir=lo,upperdir=up,workdir=wk mg 2>&1
echo "userns_overlay_rc=$?"
if [ -f mg/a ]; then
  echo modified > mg/a
  echo new > mg/b
  echo "--- upperdir listing ---"
  ls -la up/
  echo "--- upperdir/a ---"
  cat up/a 2>/dev/null
fi
umount mg 2>/dev/null
INNER
chmod +x run.sh
unshare -Urm -- ./run.sh 2>&1
echo "userns_outer_rc=$?"

# ---------- 5. fuse-overlayfs (if installed) ----------
if command -v fuse-overlayfs >/dev/null 2>&1; then
  W3=$(mktemp -d)
  cd "$W3" && mkdir lo up wk mg && echo base > lo/a
  fuse-overlayfs -o lowerdir=lo,upperdir=up,workdir=wk mg 2>&1
  echo "fuse_overlay_rc=$?"
  echo modified > mg/a 2>/dev/null
  ls -la up/ 2>/dev/null
  fusermount -u mg 2>/dev/null || umount mg 2>/dev/null
else
  echo "fuse_overlay_rc=skip"
fi

# ---------- 6. bind mount inside unshare -Urm ----------
W4=$(mktemp -d)
mkdir "$W4/s" "$W4/d" && echo marker > "$W4/s/m"
unshare -Urm -- bash -c "mount --bind $W4/s $W4/d 2>&1; cat $W4/d/m 2>/dev/null; umount $W4/d 2>/dev/null"
echo "bind_userns_rc=$?"

# ---------- 7. userxattr overlay inside userns (podman-rootless mode) ----------
W5=$(mktemp -d)
cd "$W5" && mkdir lo up wk mg && echo base > lo/a
unshare -Urm -- bash -c '
  mount -t overlay overlay -o lowerdir=lo,upperdir=up,workdir=wk,userxattr mg 2>&1
  rc=$?; echo "userxattr_overlay_rc=$rc"
  if [ $rc -eq 0 ]; then
    echo modified > mg/a
    echo new > mg/b
    echo "--- up listing ---"; ls -la up/
    echo "--- up/a ---"; cat up/a 2>/dev/null
    umount mg
  fi
'

# ---------- 8. overlay with tmpfs-backed upperdir inside userns ----------
W6=$(mktemp -d)
cd "$W6" && mkdir lo up && echo base > lo/a
unshare -Urm -- bash -c '
  mount -t tmpfs tmpfs up 2>&1
  tmrc=$?; echo "tmpfs_mount_rc=$tmrc"
  mkdir -p up/u up/w
  mount -t overlay overlay -o lowerdir=lo,upperdir=up/u,workdir=up/w,userxattr $PWD/lo.merged 2>/dev/null
  mkdir -p merged
  mount -t overlay overlay -o lowerdir=lo,upperdir=up/u,workdir=up/w,userxattr merged 2>&1
  rc=$?; echo "tmpfs_overlay_rc=$rc"
  if [ $rc -eq 0 ]; then
    echo modified > merged/a
    ls -la up/u/
    umount merged
  fi
  umount up 2>/dev/null
'

# ---------- 9. install fuse-overlayfs? ----------
echo "=== apt availability ==="
command -v apt-get && echo "apt_present=1" || echo "apt_present=0"
if command -v apt-get >/dev/null 2>&1; then
  DEBIAN_FRONTEND=noninteractive apt-get install -y fuse-overlayfs 2>&1 | tail -3
  echo "apt_install_rc=$?"
  command -v fuse-overlayfs && fuse-overlayfs --version 2>&1 | head -1 || echo "still missing"
fi

# ---------- 10. fuse-overlayfs smoke test if now available ----------
if command -v fuse-overlayfs >/dev/null 2>&1; then
  W7=$(mktemp -d)
  cd "$W7" && mkdir lo up wk mg && echo base > lo/a
  fuse-overlayfs -o lowerdir=lo,upperdir=up,workdir=wk mg 2>&1
  rc=$?; echo "fuse_overlay_rc=$rc"
  if [ $rc -eq 0 ]; then
    echo modified > mg/a
    echo new > mg/b
    ls -la up/
    fusermount -u mg 2>/dev/null || umount mg 2>/dev/null
  fi
fi

echo "=== DONE ==="
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    info = create_test_sandbox(name="overlay-probe")
    sandbox_id = info["id"] if isinstance(info, dict) else getattr(info, "id", None)
    print(f"sandbox_id={sandbox_id}")
    try:
        svc = SandboxService()
        raw = svc.get_sandbox_object(sandbox_id)
        b64 = base64.b64encode(PROBE.encode()).decode()
        # Decode script to /tmp/probe.sh, run it.
        setup = (
            f"printf %s {b64} | base64 -d > /tmp/probe.sh && "
            f"chmod +x /tmp/probe.sh && /tmp/probe.sh"
        )
        resp = raw.process.exec(f"bash -lc {shlex_quote(setup)}", timeout=180)
        stdout = getattr(resp, "result", "") or ""
        print(stdout)
        verdict(stdout)
    finally:
        if not args.keep and sandbox_id:
            try:
                delete_test_sandbox(sandbox_id)
            except Exception as exc:
                print(f"warning: delete failed: {exc}")
    return 0


def shlex_quote(s: str) -> str:
    import shlex
    return shlex.quote(s)


def verdict(out: str) -> None:
    print("\n=== VERDICT ===")
    if "kernel_overlay_rc=0" in out:
        branch = "kernel"
    elif "userns_overlay_rc=0" in out:
        branch = "userns"
    elif "fuse_overlay_rc=0" in out:
        branch = "fuse"
    else:
        branch = "none"
    print(f"overlay_branch={branch}")
    for key in ("unshare_m_rc", "unshare_urm_rc", "bind_userns_rc", "chroot_userns_rc"):
        for line in out.splitlines():
            if line.startswith(key + "="):
                print(line)
                break


if __name__ == "__main__":
    raise SystemExit(main())
