"""Helper subprocess: configure ``/etc/resolv.conf`` inside a workspace mntns.

Detection follows the symlink chain inside the WORKSPACE mntns (the daemon's
own ``/run`` may have different content). If the resolved nameserver lives in
127.0.0.0/8 (e.g., systemd-resolved stub), the workspace mntns gets a fresh
``/etc/resolv.conf`` pointing at the operator-provided fallback. The host's
file is untouched because the new mntns starts with private propagation.

R10 import discipline: same shape as ``setns_exec`` / ``setns_overlay_mount``
— stay single-threaded so ``setns(CLONE_NEWUSER)`` succeeds.

stdin: ``{"ns_fds": {"user": int, "mnt": int}, "fallback_dns": "1.1.1.1"}``
stdout: ``{"applied_fallback": bool, "previous_first_nameserver": str|null}``
"""

from __future__ import annotations

import ctypes
import json
import os
import sys

from sandbox.isolated_workspace.scripts import _setns_libc


_RESOLV_CONF = "/etc/resolv.conf"


def _first_nameserver(content: str) -> str | None:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("nameserver"):
            parts = stripped.split(None, 1)
            if len(parts) == 2:
                return parts[1].strip()
    return None


def _needs_fallback(addr: str | None) -> bool:
    return bool(addr) and addr.startswith("127.")


def _write_resolv(fallback: str) -> None:
    """Replace ``/etc/resolv.conf`` with a single-nameserver fallback.

    Docker bind-mounts ``/etc/resolv.conf`` from the container runtime into
    every container. A plain ``unlink`` on a bind-mount target returns
    EBUSY (kernel refuses to delete a mounted-over inode), so the previous
    ``unlink + create`` approach failed inside sweevo containers.

    Workaround: write the new content to a private file in /tmp, then
    ``mount --bind`` it OVER the existing ``/etc/resolv.conf``. The iws's
    mountns has private propagation, so this shadowing is invisible
    outside the iws. CAP_SYS_ADMIN inside the new user_ns is enough for
    the bind. The original host bind-mount stays put underneath.
    """
    # tempfile + uuid imports stay inside the function so the module-level
    # R10 allowlist (test_setns_exec_discipline) stays tight. Function-body
    # imports are permitted post-setns.
    import tempfile
    import uuid

    new_path = os.path.join(
        tempfile.gettempdir(), f".iws-resolv-{uuid.uuid4().hex[:12]}.conf"
    )
    fd = os.open(new_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.write(fd, f"nameserver {fallback}\n".encode("utf-8"))
    finally:
        os.close(fd)

    # ``mount --bind`` shadows the bind-mounted resolv.conf in the iws's
    # mountns only. The kernel rejects this if we mount on a path whose
    # parent dir we don't own; /etc/resolv.conf is on the container rootfs
    # which the new user_ns inherits ownership of via --map-root-user, so
    # the bind is permitted.
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    src = new_path.encode("utf-8")
    tgt = _RESOLV_CONF.encode("utf-8")
    MS_BIND = 4096
    rc = libc.mount(src, tgt, b"none", MS_BIND, None)
    if rc != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), f"mount --bind {new_path} {_RESOLV_CONF}")


def main() -> int:
    payload = json.loads(sys.stdin.read())
    ns_fds = payload["ns_fds"]
    fallback = payload["fallback_dns"]

    _setns_libc.setns(int(ns_fds["user"]), _setns_libc.CLONE_NEWUSER)
    _setns_libc.setns(int(ns_fds["mnt"]), _setns_libc.CLONE_NEWNS)

    applied = False
    prev: str | None = None
    try:
        with open(_RESOLV_CONF, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
        prev = _first_nameserver(content)
        if _needs_fallback(prev):
            _write_resolv(fallback)
            applied = True
    except FileNotFoundError:
        pass

    sys.stdout.write(
        json.dumps(
            {"applied_fallback": applied, "previous_first_nameserver": prev}
        )
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 -- helper exit reporting
        sys.stderr.write(f"configure_dns_in_ns: {exc}\n")
        sys.exit(1)
