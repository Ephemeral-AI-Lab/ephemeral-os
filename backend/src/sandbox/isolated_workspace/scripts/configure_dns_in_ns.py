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

import ctypes  # noqa: F401  -- R10 discipline parity with sibling helpers
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
    # Unlink first so we replace any symlink chain. Files are namespaced via
    # the private mntns, so the host's resolv.conf stays put.
    try:
        os.unlink(_RESOLV_CONF)
    except FileNotFoundError:
        pass
    fd = os.open(
        _RESOLV_CONF,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o644,
    )
    try:
        os.write(fd, f"nameserver {fallback}\n".encode("utf-8"))
    finally:
        os.close(fd)


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
