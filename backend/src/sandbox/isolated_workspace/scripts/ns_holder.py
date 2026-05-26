"""Long-lived PID 1 of the isolated workspace's pidns.

Two-step handshake on inherited pipe FDs (R12):
    1. Write ``ns-up\\n`` to the readiness pipe once we're in the new ns stack.
       Parent then opens our ``/proc/{pid}/ns/{net,pid,mnt,user}`` FDs and wires
       the network.
    2. Read ``net-ready\\n`` on the control pipe, bring ``lo`` up, purge any
       IPv6 default routes + disable router-advertisement acceptance so the
       v4-only MASQUERADE rule remains the sole egress, then write
       ``ready\\n``.
    3. ``pause()`` until SIGTERM (parent's exit sequence).

CLI:
    ns_holder.py <readiness_fd> <control_fd>
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys


_IPV6_CONF_ROOT = "/proc/sys/net/ipv6/conf"
_FALLBACK_IPV6_CONF_INTERFACES = ("all", "default", "lo", "eth0")


def _purge_ipv6_default_routes() -> None:
    """Remove IPv6 default routes + disable router-advertisement acceptance.

    Without this purge, a bridge-side IPv6 RA would repopulate a v6 default
    route inside the workspace and bypass the v4-only MASQUERADE filter.
    Best-effort: every command is run with ``check=False`` because some
    images strip ``ip -6`` or the sysctl write path entirely.
    """
    for iface in _ipv6_conf_interfaces():
        subprocess.run(
            ["sysctl", "-w", f"net.ipv6.conf.{iface}.accept_ra=0"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    subprocess.run(
        ["ip", "-6", "route", "flush", "default"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _ipv6_conf_interfaces() -> tuple[str, ...]:
    try:
        names = sorted(
            name
            for name in os.listdir(_IPV6_CONF_ROOT)
            if name and "/" not in name and name not in {".", ".."}
        )
    except OSError:
        return _FALLBACK_IPV6_CONF_INTERFACES
    return tuple(names) or _FALLBACK_IPV6_CONF_INTERFACES


def _rbind_proc_into_new_mntns() -> None:
    """Replace the inherited /proc with a recursive bind of the parent's /proc.

    Docker Desktop's LinuxKit kernel rejects ``mount -t proc proc /proc`` from
    inside a non-init user namespace (EPERM, even with --map-root-user, full
    CapEff inside the new user_ns, and every util-linux variant). The fresh
    /proc is what ``unshare --mount-proc`` would have produced; without it the
    spawning daemon couldn't expose a per-pid-ns /proc to setns helpers.
    rbind is allowed in the user ns and gives the holder a workable /proc
    view — the only consumers inside the new mntns are setns-exec'd shells
    that read ``/proc/self`` (uid/cwd/fd), and those work fine on a bound
    /proc since `/proc/self` follows the reading thread.

    Best-effort: failure does not abort the holder. Without the bind, the
    holder still pauses correctly and the parent can still read ns symlinks
    from ITS OWN /proc (which is what setns parents use).
    """
    subprocess.run(
        ["mount", "--rbind", "/proc", "/proc"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def main(argv: list[str]) -> int:
    readiness_fd = int(argv[1])
    control_fd = int(argv[2])

    _rbind_proc_into_new_mntns()
    os.write(readiness_fd, b"ns-up\n")

    # Test-only failure injection: exit before the parent sees ``ready``.
    if os.environ.get("EOS_ISOLATED_WORKSPACE_TEST_HOLDER_CRASH", "").strip() == "true":
        return 7

    buf = b""
    while b"\n" not in buf:
        chunk = os.read(control_fd, 64)
        if not chunk:
            return 1
        buf += chunk
    if not buf.startswith(b"net-ready"):
        return 2

    subprocess.run(["ip", "link", "set", "lo", "up"], check=False)
    _purge_ipv6_default_routes()
    os.write(readiness_fd, b"ready\n")

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    signal.pause()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
