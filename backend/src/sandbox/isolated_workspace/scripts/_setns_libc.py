"""ctypes wrapper around libc setns(2) for Python 3.11.

`os.setns` was added in Python 3.12. This module is the single place to swap
when the runtime upgrades. Imports are intentionally minimal because callers
running just before ``setns(CLONE_NEWUSER)`` must stay single-threaded.
"""

from __future__ import annotations

import ctypes
import os

CLONE_NEWNS = 0x00020000
CLONE_NEWUSER = 0x10000000
CLONE_NEWPID = 0x20000000
CLONE_NEWNET = 0x40000000


def setns(fd: int, nstype: int) -> None:
    """Call libc setns(2). Raises OSError on failure with errno set."""
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    libc.setns.argtypes = (ctypes.c_int, ctypes.c_int)
    libc.setns.restype = ctypes.c_int
    if libc.setns(fd, nstype) != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), f"setns(fd={fd}, nstype={nstype:#x})")


__all__ = [
    "CLONE_NEWNET",
    "CLONE_NEWNS",
    "CLONE_NEWPID",
    "CLONE_NEWUSER",
    "setns",
]
