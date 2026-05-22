"""In-namespace file write helper for the isolated workspace ``write_file`` op.

Invoked through setns_exec after the parent has switched into the workspace's
mntns. Writes ``content`` (base64-encoded on stdin to survive argv limits) to
``path`` atomically via O_CREAT|O_TRUNC.

CLI:
    in_ns_write.py <path>
stdin: base64-encoded bytes
"""

from __future__ import annotations

import base64
import os
import sys


def main(argv: list[str]) -> int:
    path = argv[1]
    payload = base64.b64decode(sys.stdin.buffer.read())
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
