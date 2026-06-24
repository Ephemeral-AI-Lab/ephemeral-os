# Doc 2 — The Remount Mechanism

How a workspace's overlay lower-layer stack is swapped **underneath a live,
running process group** without killing those processes and without their
filesystem view changing. Mechanism only: syscalls, `/proc`, signals, and mount
moves. No reference to code layout.

## 1. The problem

A workspace is an `overlayfs` mounted at a fixed path `R` (`workspace_root`):

```
overlay(R) = lowerdir = L1:L2:...:Ln   (read-only content layers)
             upperdir  = U              (the workspace's writable diff)
             workdir   = W
```

Processes run inside a **mount namespace** that contains this mount. They hold
live references into `R`: a current directory (`cwd`), an open root (`root`),
open file descriptors, memory-mapped files, and the mount itself.

When the layer history is **compacted** (squash), many intermediate lower layers
`L1..Ln` collapse into a smaller, **content-equivalent** stack `L1'..Lm'`
(`m ≤ n`). To reclaim the old layers we must point the mount at the new stack:

```
before: lowerdir = L1:L2:...:Ln
after:  lowerdir = L1':...:Lm'        (same bytes, fewer layers)
```

The constraint: a process may be mid-syscall against `R` at the instant of the
swap. We must guarantee it either sees the whole old mount or the whole new
mount — never a half-built one — and that after the swap its `cwd`/fds/maps
still resolve to the same content.

Two facts make this safe to attempt:

- **Content equivalence.** The compacted stack yields byte-identical file
  content. A process reading a file before and after the swap sees the same
  bytes.
- **Preserved writable layer.** `upperdir = U` and `workdir = W` are **unchanged**
  across the swap. All of the workspace's own writes survive; only the read-only
  lower plumbing is rebuilt.

## 2. Three phases

```
   QUIESCE                 STAGED SWITCH                 RESUME
 (freeze + inspect)   (build → verify → MS_MOVE →     (thaw, or cancel
                       verify → drop old)              on failure)
```

### Phase A — Quiesce the live process group

Goal: hold every process in the group perfectly still for the switch window, and
prove that none of them pins the mount in a way that would break the swap.

1. **Enumerate** the process group id (`pgid`) of each live command bound to the
   workspace.

2. **Freeze.** `killpg(pgid, SIGSTOP)`. Then poll `/proc/<pid>/stat` for every
   member until each reports stopped state (`T`/`t`), with a deadline
   (≈500 ms). If the set of member pids changes between the pre-freeze snapshot
   and the post-freeze snapshot, abort (a process forked/exited mid-freeze →
   membership is unstable). On timeout, abort.

3. **Inspect for pins.** With the group frozen, for each member read `/proc`:
   - `cwd`  → `readlink /proc/<pid>/cwd`  — does it resolve inside `R`?
   - `root` → `readlink /proc/<pid>/root` — does it resolve inside `R`?
   - fds    → `readlink /proc/<pid>/fd/*` — count any resolving inside `R`.
   - maps   → parse `/proc/<pid>/maps`    — count mapped files inside `R`.
   - mount  → parse `/proc/<pid>/mountinfo` — is `R` itself a mount point here?

   A path "inside `R`" means `path == R || path.starts_with(R)`. Any pin that the
   MS_MOVE swap cannot survive (an open `cwd`/fd/mapping into the *old* mount
   object) is a **block reason**.

4. **Decide.** Proceed to the switch only if: there is ≥1 live command, the group
   was fully quiesced (`quiesced_count == member_count`), inspection completed,
   and no block reason was recorded. Otherwise abort and go straight to Resume.

Why freeze before inspect: the inspection must be a consistent snapshot. A
running process could `chdir`/`open`/`mmap` into `R` between the check and the
switch; SIGSTOP closes that window.

### Phase B — Staged atomic switch (inside the mount namespace)

This runs as a single-threaded helper that has `setns()`'d into the **user** and
**mount** namespaces of the target so the mount operations land in the right
namespace. The switch never mutates the live mount in place; it builds the
replacement beside it and swaps with `MS_MOVE`, which is atomic per mount object.

1. **Stage.** Create two empty sibling directories next to `W`:
   `S` (staging) and `B` (rollback), names unique per pid+nanos.

2. **Build the new mount at `S`.** Mount a fresh overlay at `S` with
   `lowerdir = L1':...:Lm'`, `upperdir = U`, `workdir = W` — the **same** upper
   and work dirs as the live mount. Use mount options that remain **visible in
   `/proc/self/mountinfo`** (some kernels hide the lowerdir list with the newer
   API), because the next step must read the lowerdir back to verify it.

3. **Verify the staged mount.** Read `mountinfo` for `S` and confirm:
   - filesystem type is `overlay`;
   - the lowerdir list equals `L1':...:Lm'` exactly (count and order);
   - an optional **read probe** succeeds: read a caller-named relative file under
     `S` and, if an expected content string was supplied, confirm it matches.
   If staging does not verify, stop here — nothing has moved, the live mount at
   `R` is untouched. Tear down `S`.

4. **The switch — two `MS_MOVE`s:**
   ```
   move_mount(R → B)     # park the OLD mount object at the rollback dir
   move_mount(S → R)     # install the NEW mount object at the workspace root
   ```
   If the second move fails, immediately `move_mount(B → R)` to restore the old
   mount and abort. Because the group is frozen, no member observes the brief
   instant between the two moves.

5. **Re-verify at `R`.** Repeat the `mountinfo` + probe checks, now against `R`
   (the freshly installed mount). If it fails to verify, roll back:
   `move_mount(R → S)` then `move_mount(B → R)`, and abort.

6. **Drop the old mount.** `unmount(B)`. If the unmount fails, roll back as in
   step 5 and abort (we will not leave both mounts live). On success, the old
   mount object — and therefore the references that held the old lower layers —
   is gone, freeing those layers for reclamation.

The switch records a small state trail (`staged_switch`, `staging_verified`,
`rollback_unmounted`, any rollback error). A remount is "fully verified" only
when staging verified, the switch happened, and the old mount was cleanly
unmounted.

### Phase C — Resume (and cancellation safety)

1. **Thaw.** `killpg(pgid, SIGCONT)` for every group that was held. The processes
   continue; their `cwd`/fds/maps now resolve through the new, content-equivalent
   mount.

2. **Failure → cancel, not corrupt.** If the operation was cancelled or could not
   complete safely, the held groups are still resumed (never left frozen), but
   any command that was *party to the switch window* is cancelled rather than
   allowed to continue against an indeterminate mount. This is gated by a
   **cancellation token** plus an **affected-id set** (below).

3. **Resume is idempotent and guaranteed.** Resume runs on the normal path, on
   every error path, and on drop of the switch context, so a frozen group is
   never abandoned in SIGSTOP.

## 3. The stale-resume safety property

Quiesce/resume cycles can overlap (a new remount attempt can begin while a prior
one is unwinding). The invariant:

> **A stale resume must never cancel a command that is owned by a newer quiesce.**

Mechanism: each quiesce holds **one** cancellation token and an explicit set of
the execution ids it froze (`affected`). When a quiesce resumes-with-cancel, it
cancels **only the ids in its own `affected` set**, via the token it owns — not
"whatever is live now." A later quiesce that has adopted some of the same
processes carries a *different* token and its own id set, so the earlier
context's cancel cannot reach into it. (Equivalent to a generation/epoch guard:
identity is by owned-token + captured-id-set, not by liveness at resume time.)

## 4. Verification model (why we trust the swap)

The swap is only declared good when **all** hold:

- **Structural:** `mountinfo` at `R` shows `fs_type == overlay` and the lowerdir
  list matches the requested compacted stack exactly (count + order).
- **Behavioral:** the read probe reads a known file at a known relative path and,
  when given, matches expected content — proving the new mount actually serves
  the workspace's data, not an empty or wrong overlay.
- **Lifecycle:** staging verified **and** the MS_MOVE switch completed **and** the
  old mount unmounted cleanly with no rollback error.

Anything short of all three ⇒ `mount_verified = false` ⇒ the caller treats the
remount as failed, the old content remains the source of truth, and the affected
commands are cancelled rather than trusted.

## 5. Invariants (the whole contract in one list)

1. `upperdir`/`workdir` are identical before and after — no workspace write is
   lost.
2. The new lower stack is content-equivalent to the old — no process observes a
   content change.
3. The group is frozen across the entire build→switch→verify→drop window — no
   member acts on a half-swapped mount.
4. The switch is build-beside-then-`MS_MOVE`, never mutate-in-place — the live
   mount is replaced atomically or not at all; every failure path rolls back.
5. A frozen group is always eventually `SIGCONT`'d.
6. On any unsafe/cancelled outcome, affected commands are cancelled — never left
   running against an unverified mount.
7. Cancellation is scoped by owned-token + captured-id-set, so overlapping
   cycles cannot cancel each other's commands.

## 6. Syscall / interface summary

| Step | Primitive |
|---|---|
| Enter target namespaces | `setns(user_fd, CLONE_NEWUSER)`, `setns(mnt_fd, CLONE_NEWNS)` |
| Freeze / thaw | `killpg(pgid, SIGSTOP)` / `killpg(pgid, SIGCONT)` |
| Membership + state | read `/proc/<pid>/stat` (field: pgrp, state) |
| Pin inspection | `readlink /proc/<pid>/{cwd,root,fd/*}`, parse `/proc/<pid>/{maps,mountinfo}` |
| Build replacement | `mount(overlay)` at staging with mountinfo-visible options |
| Verify | parse `/proc/self/mountinfo` lowerdir; read a probe file |
| Atomic swap | `mount(MS_MOVE)`: `R→B`, then `S→R`; rollback `B→R` |
| Drop old | `umount(B)` |
