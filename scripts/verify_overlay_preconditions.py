#!/usr/bin/env python3
"""Verify sandbox overlay deployment preconditions."""

from __future__ import annotations

import shutil
import sys

from sandbox.overlay.mount_syscalls import mount_syscalls_supported
from sandbox.overlay.namespace_runner import detect_private_mount_namespace


def main() -> int:
    failures: list[str] = []
    if not mount_syscalls_supported():
        failures.append("mount syscall probe failed (fsopen/fsconfig/fsmount)")
    if not detect_private_mount_namespace():
        failures.append("private user/mount namespace probe failed")
    if shutil.which("unshare") is None:
        failures.append("unshare executable not found")
    if failures:
        for failure in failures:
            print(f"overlay precondition failed: {failure}", file=sys.stderr)
        return 1
    print("overlay preconditions ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
