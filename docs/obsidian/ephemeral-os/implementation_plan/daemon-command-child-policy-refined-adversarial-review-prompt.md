---
title: Daemon Command Child Policy Refined — Adversarial Review Prompt
tags:
  - ephemeral-os
  - sandbox
  - security
  - implementation-plan
  - review
status: draft
reviews: daemon-command-child-policy-refined-spec.md
---

# Adversarial Review Prompt — Shell-Security Command-Child Policy

Use this prompt to run a hostile review of
[[daemon-command-child-policy-refined-spec]] **and its implementation on `main`**.
You are not here to agree. You are here to break the design on three axes:
**architecture cleanness, no round trips, and security.** Assume the spec is
marketing copy until each claim is proven against the code.

## Operating rules

1. **Verify, don't trust.** Every "DONE"/"no change"/"generic"/"once at
   construction" claim in the spec must be confirmed by reading the actual file.
   Cite `path:line`. If the code contradicts the spec, that is a finding.
2. **Attack the seams, not the center.** The happy path (enforce mode, one
   `exec_command`) is not interesting. Hunt the edges: setup engines, `relaxed`,
   `off`, a *second* future caller of `run_shell_interactive`, a new
   `ShellOperation`, deserialization on the ns-runner, PTY paths.
3. **One finding = one falsifiable claim + evidence + severity + fix.** Severity:
   Critical (escape / policy silently not applied) / High (wrong-by-default or
   easy misuse) / Medium (smell that will rot) / Low (nit). No vibes.
4. **Prefer the destructive test.** For each risk, state the concrete input,
   caller, or refactor that would trigger it. "Could break" without a trigger is
   not a finding.

## Design under review (as claimed)

- `ShellSecurityPolicy` is a **required construction parameter** of
  `NamespaceExecutionEngine` (`new`/`with_launcher`). The engine (the shell
  runner) owns it; `run_shell_interactive` reads `self.shell_security` and stamps
  it into `NamespaceRunnerRequest`. Setup entry points (`mount_overlay`,
  `remount_overlay`, `run_file_op`) hard-code `off()`.
- `CommandConfig` and `ExecCommand` carry **no** security field. Daemon config
  flows `SandboxRuntimeConfig.shell_security` →
  `CommandOperationService::new(…, shell_security)` → engine at construction.
- Enforcement is at the command-child `pre_exec` in `shell_exec.rs`: `setpgid` →
  `no_new_privs` → targeted cap drop → seccomp-lite deny table → `execve`. BPF is
  built pre-`fork` in a `OnceLock`.
- Modes: `enforce` (default) / `relaxed` (drops namespace denials) / `off` (cap
  drop only, no seccomp).

Key files: `namespace-execution/src/engine.rs`,
`namespace-process/src/runner/shell_security.rs`,
`namespace-process/src/runner/shell_exec.rs`,
`operation/src/command/service/{core,exec,exec_command}.rs`,
`operation/src/services.rs`, `workspace/src/namespace/mod.rs`,
`sandbox-config/src/configs/manager.rs`, `sandbox-daemon/src/serve.rs`.

## Axis 1 — Architecture cleanness

Probe and answer with evidence:

1. **Ownership leakage.** Grep for `shell_security` across `operation/` and prove
   `CommandConfig`/`ExecCommand` truly hold nothing. Does any command-layer type,
   accessor, or test still thread the policy? If the value only *passes through*
   `CommandOperationService::new`, is that materially better than the field it
   replaced, or just relocated?
2. **SRP of the engine.** `NamespaceExecutionEngine` runs *both* user shells and
   privileged setup (mount/remount/file). It now carries a `shell_security` field
   that exactly one of its four entry points reads; the other three hard-code
   `off()`. Is that one field on a multi-purpose type a clean design or a smell?
   Would splitting "shell runner" from "setup runner" be cleaner — or overkill?
   Take a position.
3. **The `off()` setup engine.** `workspace/src/namespace/mod.rs` builds an engine
   with `off()`. Is a setup-only engine *carrying a shell policy at all* a wart?
   What stops a future edit from calling `run_shell_interactive` on that engine
   and running untrusted code with `off()`? Is there a compile-time or review-time
   guard, or only convention?
4. **Type placement / dependency direction.** `ShellSecurityPolicy` lives in
   `namespace-process::runner::protocol` but is a required parameter of the
   `namespace-execution` engine and is named by `operation`, `workspace`,
   `daemon`, `config`. Is the dependency arrow clean? Should the engine's crate
   re-export it so callers don't reach past it into `namespace-process`? Flag any
   layering inversion.
5. **Naming.** The engine also runs non-shell ops, yet the policy is
   `shell_security` and the field sits on the general engine. Does the name
   mislead? Is `shell_security` right, or does it over-promise ("only shells")
   / under-describe (it's really the *user-command* child)?
6. **Dead configuration.** Is `relaxed` reachable by any real workload today, or
   is it speculative config surface (YAGNI) that widens the security envelope for
   no current user? Should it exist yet?
7. **Boundary law.** Confirm the change respects `README.md`'s boundary law
   (protocol vocab in `sandbox-protocol`; runtime specs in `runtime/operation`).
   Did the policy value or type cross a boundary it shouldn't?

## Axis 2 — No round trips / no waste

Probe and answer with evidence:

1. **Bound once or re-derived?** Prove the policy is resolved a single time
   (engine construction) and not recomputed, re-read from config, or re-mapped
   per `exec_command`. Show the one construction site vs. the per-command path.
2. **Request-path allocation.** Is anything allocated, cloned, formatted, or
   locked on the per-command path *because of* the policy? Is `ShellSecurityPolicy`
   `Copy`, and is it copied (cheap) rather than cloned/boxed? Check
   `build_request` and `run_shell_interactive`.
3. **BPF build cost.** Confirm the seccomp program is built **before `fork`** and
   cached (`OnceLock`), not rebuilt per command or inside `pre_exec`. Prove the
   child closure is async-signal-safe (no alloc/`Vec`/format). Any per-exec
   `SeccompFilter::new`/`BpfProgram::try_from` on the hot path is a finding.
4. **Boundary crossings.** Count the hops the policy takes from config to
   enforcement (daemon → operation → namespace-execution → namespace-process →
   ns-runner process). Is each hop necessary, or is there a redundant carry? The
   ns-runner is a separate process, so the value *must* ride the serialized
   `NamespaceRunnerRequest` — confirm that's the only serialization and it isn't
   also passed a second way.
5. **Redundant storage.** Is the policy stored in more than one live place
   (engine field, request, config)? Each copy is a sync hazard — justify or flag.
6. **Setup-path overhead.** The three setup entry points pass `off()`. Do they
   still build/install any filter, or is `off()` a true no-op (no seccomp syscall,
   no cap work beyond what setup needs)? A wasted syscall on the setup path is a
   finding.

## Axis 3 — Security

Probe and answer with evidence. This axis outranks the other two — a clean,
zero-round-trip design that fails to constrain the child is a failure.

1. **Is the guarantee real?** The spec claims "no engine without a policy, so
   every `run_shell_interactive` caller is covered." Try to defeat it: is there a
   `Default` for the engine, a builder, a `#[cfg(test)]` shortcut, or any
   constructor that yields an engine without an explicit policy? Can a caller pass
   `off()` where `enforce` was intended and have it look correct?
2. **Does the policy reach the child?** Trace the value end-to-end: engine field →
   `build_request` → serialized `NamespaceRunnerRequest` → ns-runner deserialize →
   `shell_exec.rs` `prepare`/`apply`. Find any link where it can be dropped,
   defaulted (`#[serde(default)]`), or silently become `off()` across the process
   boundary. What does the ns-runner do if the field is missing/garbage?
3. **Privileged-path containment.** Prove **no user command code** ever runs on a
   path that skips `apply_shell_security_policy`: daemon, ns-holder, ns-runner
   helper, `install_pgid_leader_hook`, mount/remount/file runners, and any PTY
   path. If any of these can exec attacker-controlled argv, that is Critical.
4. **Order-of-operations.** Confirm `no_new_privs` precedes seccomp (required),
   cap drop precedes seccomp (needs `CAP_SETPCAP`), and `execve`/dynamic-linker
   syscalls remain allowed. A filter that blocks the program from starting, or a
   seccomp-before-NNP ordering, is a finding.
5. **Denylist gaps (own the residual).** From the spec's §10/§20: `ioctl`
   (`TIOCSTI`), future/obscure syscalls a denylist can't auto-close, and rising
   `io_uring` usage. Are these acknowledged and bounded, or hand-waved? Given the
   allowlist was deleted, what is the actual compensating control, and does it
   exist *today* or only in Phase 3?
6. **Mode footguns.** `relaxed` re-enables `unshare`/`setns`/`clone(NEW*)` — the
   userns-escape primitives. Is it clearly gated, non-default, and documented as
   security-reducing? `off` disables seccomp entirely — can it be set by accident
   (default drift after the rename)? Confirm `enforce` is still the default in
   `manager.rs` and `prd.yml` after the `command_security` → `shell_security`
   rename; a rename that flipped a default is Critical.
7. **Capability policy.** Kept caps include `NET_RAW`, `MKNOD`, `DAC_READ_SEARCH`.
   For each kept cap, is it justified against a concrete workload, and is the
   matching syscall-level control (mode-filtered `mknod`, `open_by_handle_at`
   denial) actually present? A kept cap with no seccomp backstop is a finding.
8. **Rename integrity.** The rename moved identifiers, a module file
   (`command_security.rs` → `shell_security.rs`), and a config key
   (`manager.command_security.mode` → `manager.shell_security.mode`). Find anything
   left behind: a stale identifier, an unrenamed `#[serde(rename)]`, a config file
   or e2e harness still emitting `command_security` that will now be *silently
   ignored* (or rejected by `deny_unknown_fields`) — meaning the policy reverts to
   default without warning. The e2e Python dir is known-unrenamed; confirm whether
   it sets the config key and would break or mis-configure.

## Required output

Produce, in this order:

1. **Verdict table** — one row per axis (Architecture / Round-trips / Security),
   each `PASS | PASS-WITH-RISKS | FAIL`, with a one-line justification.
2. **Findings** — sorted by severity. Each: `[SEV] title` · claim · evidence
   (`path:line`) · concrete trigger · recommended fix.
3. **Top 3 must-fix** before this is considered done.
4. **What the spec oversells** — any claim not backed by the code.
5. **One question** you could not resolve from the code that a human must answer.

Do not soften. If it is clean, say so in one line and spend the rest on the
sharpest risk you can find anyway.
