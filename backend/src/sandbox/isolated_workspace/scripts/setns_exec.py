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
    payload = json.loads(sys.stdin.read())
    ns_fds = payload["ns_fds"]
    argv = list(payload["argv"])

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

    pid = os.fork()
    if pid == 0:
        os.execvp(argv[0], argv)
        os._exit(127)
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status)


if __name__ == "__main__":
    sys.exit(main())
