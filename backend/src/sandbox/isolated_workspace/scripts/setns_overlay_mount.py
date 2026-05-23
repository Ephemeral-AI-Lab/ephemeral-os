"""Helper subprocess: setns into a workspace mntns, then mount overlayfs.

The mount must happen inside the workspace mntns so the host stays clean.
setns into the user namespace first grants CAP_SYS_ADMIN inside that ns
(no real root needed); setns into the mntns then switches the calling
thread's mount table.

Code reuse
----------
Once both ``setns`` calls have completed, the single-thread requirement for
``setns(CLONE_NEWUSER)`` no longer applies. Path validation / FD pinning and
the mount sequence are delegated to
:mod:`sandbox.execution.overlay.kernel_mount` — the same implementation the
daemon's OCC overlay uses. Importing it is *deferred* until after setns so
module-level R10 discipline is preserved (``kernel_mount`` transitively pulls
``subprocess``, which is forbidden pre-setns).

R10 imports
-----------
Module-level imports MUST match
``test_setns_exec_discipline``'s allowlist. Function-body imports that run
*after* the setns calls are intentionally outside the fence.

stdin payload (one JSON object):
    {
        "ns_fds":    {"user": int, "mnt": int},
        "target":    "/testbed",
        "lowerdirs": ["/var/lib/eos/layer_stack/<sha>", ...],
        "upperdir":  "/var/lib/eos/.../upper",
        "workdir":   "/var/lib/eos/.../work",
    }
"""

from __future__ import annotations

import ctypes  # noqa: F401  -- R10 discipline parity with sibling helpers
import json
import sys

from sandbox.isolated_workspace.scripts import _setns_libc


def main() -> int:
    payload = json.loads(sys.stdin.read())
    ns_fds = payload["ns_fds"]
    target = payload["target"]
    lowerdirs = payload["lowerdirs"]
    upperdir = payload["upperdir"]
    workdir = payload["workdir"]

    if not lowerdirs:
        sys.stderr.write("setns_overlay_mount: empty lowerdir stack\n")
        return 3

    # Order matters: user first (privilege change), then mnt (mount table).
    _setns_libc.setns(int(ns_fds["user"]), _setns_libc.CLONE_NEWUSER)
    _setns_libc.setns(int(ns_fds["mnt"]), _setns_libc.CLONE_NEWNS)

    # Deferred import: setns is done. kernel_mount transitively pulls
    # subprocess/logging which would break setns(CLONE_NEWUSER) at
    # module-load time; here they're harmless because the single-thread
    # requirement no longer applies post-setns.
    from pathlib import Path

    from sandbox.execution.overlay.kernel_mount import (
        mount_overlay,
        validate_mount_inputs,
    )

    mount_inputs = validate_mount_inputs(
        workspace_root=Path(target),
        layer_paths=tuple(Path(p) for p in lowerdirs),
        upperdir=Path(upperdir),
        workdir=Path(workdir),
    )
    try:
        mount_overlay(
            workspace_root=mount_inputs.workspace_root,
            layer_paths=mount_inputs.layer_paths,
            upperdir=mount_inputs.upperdir,
            workdir=mount_inputs.workdir,
            pass_fds=mount_inputs.fds,
        )
    finally:
        mount_inputs.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except OSError as exc:
        sys.stderr.write(f"setns_overlay_mount: {exc}\n")
        sys.exit(2)
    except Exception as exc:  # noqa: BLE001 -- helper exit reporting
        sys.stderr.write(f"setns_overlay_mount: {exc}\n")
        sys.exit(1)
