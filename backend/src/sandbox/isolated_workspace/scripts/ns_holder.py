"""Long-lived PID 1 of the isolated workspace's pidns.

Two-step handshake on inherited pipe FDs (R12):
    1. Write ``ns-up\\n`` to the readiness pipe once we're in the new ns stack.
       Parent then opens our ``/proc/{pid}/ns/{net,pid,mnt,user}`` FDs and wires
       the network.
    2. Read ``net-ready\\n`` on the control pipe, set ``lo`` up, write ``ready\\n``.
    3. ``pause()`` until SIGTERM (parent's exit sequence).

CLI:
    ns_holder.py <readiness_fd> <control_fd>
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys


def main(argv: list[str]) -> int:
    readiness_fd = int(argv[1])
    control_fd = int(argv[2])

    os.write(readiness_fd, b"ns-up\n")

    buf = b""
    while b"\n" not in buf:
        chunk = os.read(control_fd, 64)
        if not chunk:
            return 1
        buf += chunk
    if not buf.startswith(b"net-ready"):
        return 2

    subprocess.run(["ip", "link", "set", "lo", "up"], check=False)
    os.write(readiness_fd, b"ready\n")

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    signal.pause()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
