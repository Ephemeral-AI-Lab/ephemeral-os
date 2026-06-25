# §8 Resolution — `--workspace-root` visibility under one-shot `exec_command`

**Status:** resolved (documented semantics) · relates to spec §8.

## Decision

For v1 of the Docker runtime, one-shot `exec_command` (no
`--workspace-session-id`) runs under `WorkspaceProfile::HostCompatible`, whose
overlay is `move_mount`'d onto the container `/workspace` with lowerdirs sourced
only from the runtime layer stack (`overlay/src/kernel_mount.rs`,
`namespace-process/src/runner/setns/mount_overlay.rs`). The bind-mounted host
`--workspace-root` is therefore **shadowed** by the overlay mount target, and the
pre-existing files a user placed at `--workspace-root` are **not** guaranteed to
be visible to a one-shot command. `pwd` returns the mount point `/workspace`;
its contents are the overlay's, not the host directory's.

This is daemon/`workspace`-crate behavior, **not** something the Docker provider,
manager lifecycle, or transport layers change. Capturing the host workspace into
a lowerdir is a `workspace`-crate change tracked as a separate follow-up (spec
§8 option (b)).

## How the oneshot matrix reflects this

The oneshot `exec_command` matrix
(`tests/runtime/command/exec_command/oneshot/`) is written so that **no case
depends on reading pre-existing files at `--workspace-root`**:

- OS-EXEC-001 asserts `pwd` returns an absolute path (`/workspace`), not specific
  contents.
- OS-EXEC-002/008/010/011 generate their own output (`printf`, shell loops,
  `/dev/zero`) inside the sandbox.
- OS-EXEC-003/004/005/006/007/009/012 assert lifecycle/status semantics only.

The mount still works on Docker Desktop because lowerdir/upper/work all live on
`/eos/*` (the container's native fs); only the overlay mount *target* is the
bind-mounted `/workspace` (spec §8 "Corollary — overlay on Docker Desktop is
SAFE"). A fresh sandbox with an empty layer stack still yields a valid overlay.

## What flips this to "files visible"

If the `workspace` crate later captures `workspace_root` into a lowerdir (scan
→ layer → lowerdir), one-shot commands would then see the user's files, and the
matrix can add a case asserting a seeded file is readable. Until then, treat
`--workspace-root` as the bind target for the daemon's own writes, not as a
read-through of host content.
