"""Helper subprocess: setns into a held namespace stack, then fork+exec a command.

R10 import discipline: the module-level import set is exactly
``{os, sys, ctypes, json, sandbox.isolated_workspace.scripts._setns_libc}``
(json for payload parsing). ``setns(CLONE_NEWUSER)`` requires the calling
thread to be the only thread in the process; importing ``logging`` /
``asyncio`` / ``subprocess`` (any of which silently spin background threads)
breaks the syscall with EINVAL. Enforced by
``test_setns_exec_helper_imports_are_minimal``.

stdin payload (one JSON object per invocation):
    {
        "ns_fds": {"user": int, "mnt": int, "pid": int, "net": int},
        "argv": [str, ...],
        "stdin_b64": str (optional, base64),
    }

The parent process passes ns FDs via inheritable FDs (the JSON carries their
numeric values). After setns, this process forks; the child execs ``argv``.
"""

from __future__ import annotations

import ctypes  # noqa: F401  -- imported for R10 discipline parity with _setns_libc
import json
import os
import sys

from sandbox.isolated_workspace.scripts import _setns_libc


def main() -> int:
    payload = json.loads(sys.stdin.buffer.read())
    ns_fds = payload["ns_fds"]
    argv = list(payload["argv"])
    stdin_b64 = payload.get("stdin_b64") or ""

    # Order matters: user, mnt, pid, net. PID setns affects only descendants;
    # call before fork().
    for key, nstype in (
        ("user", _setns_libc.CLONE_NEWUSER),
        ("mnt", _setns_libc.CLONE_NEWNS),
        ("pid", _setns_libc.CLONE_NEWPID),
        ("net", _setns_libc.CLONE_NEWNET),
    ):
        fd = ns_fds.get(key)
        if fd is None:
            continue
        _setns_libc.setns(int(fd), nstype)

    # If the caller supplied stdin bytes, decode them and pipe to the child's
    # stdin via an anonymous pipe so the in-ns command sees them on its own
    # fd 0. Without this the child inherits our (already-drained) stdin and
    # blocks/EOFs on read.
    import base64
    stdin_bytes = base64.b64decode(stdin_b64) if stdin_b64 else b""
    child_stdin_r = -1
    child_stdin_w = -1
    if stdin_bytes:
        child_stdin_r, child_stdin_w = os.pipe()

    pid = os.fork()
    if pid == 0:
        if child_stdin_r >= 0:
            os.dup2(child_stdin_r, 0)
            os.close(child_stdin_r)
            os.close(child_stdin_w)
        os.execvp(argv[0], argv)
        os._exit(127)
    if child_stdin_r >= 0:
        os.close(child_stdin_r)
        # Write the payload to the child's stdin then close so the child sees EOF.
        try:
            os.write(child_stdin_w, stdin_bytes)
        finally:
            os.close(child_stdin_w)
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status)


if __name__ == "__main__":
    sys.exit(main())
