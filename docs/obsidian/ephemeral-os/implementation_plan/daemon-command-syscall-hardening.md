---
title: Daemon Command Syscall Hardening
tags:
  - ephemeral-os
  - sandbox
  - security
  - implementation-plan
status: superseded
superseded-by: daemon-command-child-policy-refined-spec.md
---

# Daemon Command Syscall Hardening

> **Superseded design record (operation-layout exempt, 2026-07-11):** Commands
> and source paths below are retained for decision history, not current use.

## Decision

Do not use gVisor or require a special sandbox image. Add one Linux-only command
hardening hook to the `sandbox-daemon` binary before it `exec`s user commands:

1. set `no_new_privs`
2. drop command-child capabilities
3. install a small seccomp filter

This is not a userspace kernel. It is the smallest useful patch that blocks
commands from using kernel APIs that can undo the sandbox's namespace and mount
assumptions.

## Current Design Check

EphemeralOS already has isolation, but not gVisor-style kernel isolation.

Current boundaries:

- Docker container boundary around the daemon.
- Read-only host workspace bind during sandbox creation.
- Host bind unmounted after the layerstack base is built.
- Per-command mount, PID, and user namespaces.
- Optional per-workspace network namespace.
- Overlay workspace rooted in sandbox-owned storage.

Current gap:

- The sandbox container is privileged by default.
- User commands run after namespace setup, but without a daemon-installed syscall
  policy.
- A malicious command can try mount, namespace, module, BPF, ptrace, keyring, or
  cross-process syscalls that normal build/test commands do not need.

So the answer is: **the sandbox has namespace isolation, but not a strong
kernel-level syscall boundary between untrusted command code and the Linux
kernel available to the container.**

## gVisor Lesson To Keep

gVisor's security model is useful as a principle, not as a dependency:

- gVisor minimizes direct application access to the host system API through the
  Sentry model: https://gvisor.dev/docs/architecture_guide/security/
- gVisor's systrap platform uses seccomp traps to intercept syscalls:
  https://gvisor.dev/docs/architecture_guide/platforms/

EphemeralOS should not reimplement that. The mini-patch should only reduce the
dangerous syscall surface for user commands.

## Universal Host Rule

Apply the policy inside the Linux sandbox process. That makes it host-portable:

| Host | Where the policy runs | Result |
|---|---|---|
| Linux Docker Engine | sandbox container on the host Linux kernel | protects the native Linux kernel/container boundary |
| macOS Docker Desktop | sandbox container inside Docker's Linux VM | protects the Linux VM and Docker file-sharing boundary |
| Windows Docker Desktop / WSL2 | sandbox container inside the Linux VM/WSL kernel | protects the Linux VM/WSL and Docker file-sharing boundary |

This does not depend on packages inside the image. The daemon binary carries the
policy.

## Patch Point

Patch only the command child path:

```text
crates/sandbox-runtime/namespace-process/src/runner/shell_exec.rs
```

The existing `Command::pre_exec` hook already sets the command process group.
Extend that same child-only hook:

```text
setpgid(0, 0)
apply_command_security_policy()
exec shell
```

Do not apply this to daemon startup, workspace creation, namespace holder setup,
or overlay mounting. Those paths still need privileged operations before the
user command starts.

## v1 Syscall Policy

Default action: allow.

Denied with `EPERM`:

| Category | Syscalls |
|---|---|
| mount mutation | `mount`, `umount2`, `pivot_root`, `move_mount`, `open_tree`, `fsopen`, `fsconfig`, `fsmount`, `fspick`, `mount_setattr` |
| namespace mutation | `setns`, `unshare`, `clone` only when namespace flags are present |
| kernel/module control | `init_module`, `finit_module`, `delete_module`, `kexec_load`, `kexec_file_load`, `reboot` |
| privileged kernel surfaces | `bpf`, `perf_event_open`, `userfaultfd`, `fanotify_init` |
| process escape/introspection | `ptrace`, `process_vm_readv`, `process_vm_writev` |
| special files / handles | `mknod`, `mknodat`, `open_by_handle_at` |
| keyrings | `add_key`, `request_key`, `keyctl` |

Denied with `ENOSYS`:

| Syscall | Reason |
|---|---|
| `clone3` | seccomp cannot inspect the pointed-to `clone_args` without more machinery; returning `ENOSYS` lets runtimes fall back to `clone` |

Do not block normal file, process, pipe, socket, fork, exec, read, write, mmap,
futex, clock, signal, or wait syscalls.

## Capability Policy

Before seccomp, drop capabilities for the command child:

- effective
- permitted
- inheritable
- ambient, where supported

The daemon and setup helpers keep their existing privileges. Only user command
descendants lose them.

## Minimal Implementation Shape

Add one Linux-only helper module under `namespace-process`:

```text
runner/command_security.rs
```

Responsibilities:

- `apply_command_security_policy() -> io::Result<()>`
- `set_no_new_privs()`
- `drop_capabilities()`
- `install_seccomp_filter()`

Use existing dependencies only:

- `libc`
- `std::io`

No new crate. No config flag in v1. A security boundary should be on by default;
add an escape hatch only if a real supported workload breaks.

## Compatibility Contract

Must keep working:

- shell startup
- package manager reads/writes inside the overlay
- compilers and test runners
- process spawning
- pipes, PTYs, and signal handling
- TCP/UDP according to the workspace network profile
- one-shot `exec_command`
- session `exec_command`

Expected to fail with permission errors:

```sh
mount -t tmpfs tmpfs /tmp/x
umount /eos
unshare -m true
python -c 'import ctypes; libc=ctypes.CDLL(None); print(libc.keyctl(0,0,0,0,0))'
```

## Verification

Local build checks:

```sh
cargo test -p sandbox-runtime-namespace-process
cargo fmt
```

Required repository check after implementation:

```sh
bin/start-sandbox-docker-gateway --rebuild-binary
```

Live sandbox checks must use `sandbox-cli`:

```sh
bin/sandbox-cli manager create_sandbox --image IMAGE --workspace-root PATH
bin/sandbox-cli runtime exec_command "echo OK"
bin/sandbox-cli runtime exec_command "mkdir -p /tmp/x && mount -t tmpfs tmpfs /tmp/x"
bin/sandbox-cli runtime exec_command "unshare -m true"
bin/sandbox-cli runtime exec_command "umount /eos"
```

Pass criteria:

- `echo OK` succeeds.
- `mount`, `unshare`, and `umount /eos` fail.
- daemon stays alive after denied syscalls.
- sandbox destroy still cleans up container and volumes.

## Non-Goals

- No gVisor / `runsc`.
- No custom userspace kernel.
- No image-specific package installation.
- No host-specific macOS, Windows, or Linux setup.
- No broad Docker provider refactor in v1.
- No configurable policy until there is a real compatibility failure.

## Later, Only If Needed

- Add an allow/deny telemetry counter for blocked syscall names.
- Split privileged setup from command runtime so the Docker container itself can
  run non-privileged.
- Add a provider option for stronger host-side runtimes when available.
