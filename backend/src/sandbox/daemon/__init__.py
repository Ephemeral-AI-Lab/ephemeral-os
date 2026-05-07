"""In-sandbox daemon modules that ship inside the runtime bundle.

Strictly the bytes that execute INSIDE a sandbox: ``rpc/`` for the AF_UNIX
server and dispatcher, ``handlers/`` for OP_TABLE entries, ``services/`` for
in-process dependencies, and ``overlay_shell/`` for overlay shell runtime.
Host-side plumbing lives under :mod:`sandbox.host`.
"""

from __future__ import annotations

__all__: list[str] = []
