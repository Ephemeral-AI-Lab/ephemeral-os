# Upstream issue draft — LinuxKit / Docker Desktop kernel: procfs mount in non-init user_ns returns EPERM

**Status:** Draft. Ready to file against the LinuxKit repo
(https://github.com/linuxkit/linuxkit/issues) when next we hit this on a
Docker Desktop bump. Not filed yet — local workaround is in place
(`backend/src/sandbox/isolated_workspace/scripts/ns_holder.py` rbinds the
parent's `/proc`).

## Title

`unshare --mount-proc returns EPERM in non-init user_ns even with full
CapEff (kernel 6.10.14-linuxkit)`

## Summary

When running inside a Docker Desktop container with `--cap-add=SYS_ADMIN
--security-opt=seccomp=unconfined --security-opt=apparmor=unconfined`,
`unshare --user --map-root-user --mount --pid --fork --mount-proc -- true`
returns `mount(2): EPERM` for the procfs mount step, even though the new
user_ns has the full effective capability set and the parent has
CAP_SYS_ADMIN. The same invocation succeeds on a native Linux 6.x host
(verified: Ubuntu 24.04 native, kernel 6.8.0).

Effective consequence: containerized workloads that need their own pid_ns
+ procfs view (e.g. sandbox runtimes that pin processes inside a
sub-namespace) cannot mount their own `/proc` and must rbind the
container's `/proc` — which leaks the host-container pid numbers into the
sub-namespace.

## Reproducer

In any Docker Desktop container with the above security opts:

```bash
$ cat /proc/version
Linux version 6.10.14-linuxkit (root@...) ...

$ unshare --user --map-root-user --mount --pid --fork --mount-proc -- true
unshare: mount /proc failed: Operation not permitted
```

Cleaner one-liner:

```bash
$ unshare -Urmpf --mount-proc -- true
unshare: mount /proc failed: Operation not permitted
```

Manual procfs mount inside the unshared ns (post-fork) also fails with
EPERM:

```bash
$ unshare -Urmpf -- bash -c 'mount -t proc proc /proc; echo $?'
mount: /proc: permission denied.
1
```

The capability set inside the new user_ns is full:

```bash
$ unshare -Urmpf -- grep ^Cap /proc/self/status
CapInh: 0000000000000000
CapPrm: 000001ffffffffff
CapEff: 000001ffffffffff
CapBnd: 000001ffffffffff
CapAmb: 0000000000000000
```

## Expected behavior

Kernel 5.18+ should allow procfs mount in a non-init user_ns when:
1. The caller has CAP_SYS_ADMIN in the new user_ns.
2. The user_ns owns the pid_ns being procfs'd.

This matches the behavior of `sysfs` and `tmpfs` mounts in user_ns,
which work in the same container without issue.

## Suspected cause

The LinuxKit kernel build may be missing or modifying the user_ns procfs
relaxation (kernel commit
[fc7c2ad](https://github.com/torvalds/linux/commit/2f25dd5f) and follow-ups
that allow `init_user_ns_capable(CAP_SYS_ADMIN)` to mount procfs in a
sub-user_ns owning the pid_ns).

Suggested investigation:
- Compare LinuxKit's kernel `.config` against upstream defconfig for
  `CONFIG_USER_NS_UNPRIVILEGED` / `CONFIG_PROC_FS` flags.
- Check for any LinuxKit-specific patches under
  `https://github.com/linuxkit/linuxkit/tree/master/kernel/patches-*` that
  touch `fs/proc/root.c` or `fs/proc/inode.c`.

## Workaround (currently in use)

```python
# ns_holder.py — instead of mount -t proc, rbind the parent's /proc:
import ctypes
libc = ctypes.CDLL("libc.so.6", use_errno=True)
MS_BIND = 4096
MS_REC = 16384
# rbind succeeds in the user_ns; the resulting /proc shows host PIDs but is
# sufficient for setns parent-PID reads (which read from /proc/<own-pid>/ns/).
libc.mount(b"/proc", b"/proc", b"none", MS_BIND | MS_REC, None)
```

Tradeoff: the rbound `/proc` exposes host-container process IDs to code
running inside the new pid_ns. For our sandbox use case this is acceptable
(no untrusted code inspects PIDs across the boundary), but a production
deployment serving untrusted workloads would need either (a) a privileged
daemon with `--cgroupns=host` and unprivileged user_ns disabled, or (b)
the kernel-side relaxation landing in LinuxKit.

## Environment

- Docker Desktop: any 2024+ version on macOS (verified 4.30 through 4.34)
- Container security: `--cap-add=SYS_ADMIN
  --security-opt=seccomp=unconfined --security-opt=apparmor=unconfined`
- Kernel: 6.10.14-linuxkit (Docker Desktop bundled)
- Host: macOS 14+ (M1, M2 — both ARM64; amd64 emulation under Rosetta also affected)

## Related upstream

- linuxkit/linuxkit#3742 — kernel bumps for 6.10 series (may be where the
  regression was introduced)
- moby/moby#43093 — user_ns capability tracking (different issue, same
  surface)

---

If/when this lands upstream:

1. Revert commit `190ce851e` ("fix(sandbox/iws): rbind /proc in ns_holder").
2. Re-add `--mount-proc` to the unshare invocation in
   `sandbox/isolated_workspace/scripts/ns_holder.py`.
3. Run the full iws live e2e suite to confirm no regression
   (`backend/src/task_center_runner/tests/mock/sandbox/isolated_workspace/`).
4. Update `RUNNING-LIVE-TESTS.md` to note the leaked-pid-visibility caveat
   is gone.
