---
title: Adversarial Review Prompt — Shell-Security Knob Removal & Relaxed Deletion
tags:
  - ephemeral-os
  - sandbox
  - security
  - implementation-plan
  - adversarial-review
status: draft
reviews:
  - daemon-command-child-policy-refined-spec.md
---

# Adversarial Review Prompt — Shell-Security Knob Removal & Relaxed Deletion

Use this prompt to run a hostile review of the change that **deleted the
`relaxed` shell-security mode and removed the operator-facing
`manager.shell_security.mode` config knob**, hardcoding the command child to
`enforce`. Review the **implementation on `main` (working tree)** and its
reconciliation of [[daemon-command-child-policy-refined-spec]]. You are not here
to agree. Break the change on three axes: **architecture cleanness, no
round-trips / no dead code, and security.** Assume every "removed", "hardcoded",
"internal-only", and "fail-closed" claim is marketing until proven against the
code.

## Operating rules

1. **Verify, don't trust.** Every "removed"/"hardcoded enforce"/"internal-only"/
   "fail-closed"/"no dead code" claim must be confirmed by reading the actual
   file. Cite `path:line`. If the code contradicts this prompt, that is a finding.
2. **Attack the seams, not the center.** The happy path (`from_config` builds an
   engine, one `exec_command` runs enforce) is not interesting. Hunt the edges:
   the retained `CommandOperationService::new(shell_security)` param, the
   still-public `ShellSecurityPolicy::off()`, the daemon-vs-gateway `manager`
   split, the shared `prd.yml`, the ns-runner wire default, a half-removed
   `relaxed` BPF path, `#[cfg(target_os = "linux")]` code that only CI compiles.
3. **One finding = one falsifiable claim + evidence + severity + fix.** Severity:
   Critical (command child can run non-`enforce` / policy silently dropped) /
   High (wrong-by-default, silent misconfig, or easy downgrade) / Medium (smell
   that will rot) / Low (nit). No vibes.
4. **Prefer the destructive test.** For each risk, state the concrete input,
   caller, config, or refactor that triggers it. "Could break" without a trigger
   is not a finding. Note which claims are only *compile-verified* on macOS (the
   `shell_security` module is Linux-gated) versus actually *run*.

## Change under review (as claimed)

- **`relaxed` is deleted.** `ShellSecurityMode` is now `{ Enforce, Off }`
  (`namespace-process/src/runner/protocol.rs`); `ShellSecurityPolicy::relaxed()`,
  `RELAXED_PROGRAMS`, and the `mode` parameter on `build_seccomp_programs` /
  `build_errno_filter` are gone (`.../runner/shell_security.rs`). The namespace
  denials (`setns`/`unshare`/`clone(NEW*)`) are now **unconditional** in the one
  built filter.
- **The operator knob is gone.** `ShellSecurityConfig` and the config-side
  `ShellSecurityMode` are deleted; `ManagerConfig` holds only
  `docker` (`sandbox-config/src/configs/manager.rs`). The daemon no longer loads
  the `manager` section at all (`sandbox-daemon/src/serve.rs`).
- **Command child hardcoded `enforce`.** `SandboxRuntimeConfig` lost its
  `shell_security` field; `SandboxRuntimeOperations::from_config` passes
  `ShellSecurityPolicy::enforce()` to `CommandOperationService::new`
  (`operation/src/services.rs`). `CommandOperationService::new` **still takes** a
  `shell_security` param (kept as a test seam).
- **`off()` is internal-only.** It survives as a `const fn` used by the setup
  engine (`workspace/src/namespace/mod.rs`) and tests; it is not selectable via
  config.
- **`command_security` naming scrubbed.** The live e2e dir is `git mv`'d to
  `e2e/runtime/shell_security/`, the test rewritten
  enforce-only (no `E2E_COMMAND_SECURITY_MODE` branching), `prd.yml` no longer
  carries `manager.shell_security`.
- **Spec reconciled.** §1/§3/§6/§8/§9/§10/§12/§15/§18/§19/§20 updated to describe
  an unconditional-`enforce`, no-knob design.

Key files: `namespace-process/src/runner/protocol.rs`,
`namespace-process/src/runner/shell_security.rs`,
`namespace-process/tests/unit/runner/shell_security.rs`,
`sandbox-config/src/configs/manager.rs`, `sandbox-daemon/src/serve.rs`,
`operation/src/services.rs`, `operation/src/command/service/core.rs`,
`namespace-execution/src/engine.rs`, `workspace/src/namespace/mod.rs`,
`config/prd.yml`, `sandbox-gateway/src/gateway/main.rs`,
`e2e/runtime/shell_security/`.

## Axis 1 — Architecture cleanness

Probe and answer with evidence:

1. **The retained `new` param.** `CommandOperationService::new` still takes
   `shell_security: ShellSecurityPolicy` but production always passes
   `enforce()`. Is that clean (a legitimate test seam that lets Linux-CI tests
   pass `off()` to avoid seccomp) or dead flexibility that reopens the very
   downgrade the knob-removal was meant to close? Prove how many production
   callers exist. Take a position: keep the param, or hardcode `enforce()` inside
   the service and drop it?
2. **Where the policy decision lives.** The command child's entire security
   posture is now a bare `ShellSecurityPolicy::enforce()` literal inside a wiring
   function (`services.rs::from_config`). Is a security invariant buried where no
   reader would look? Should it be a named constant / documented invariant on the
   command service instead?
3. **`off()` still public, "internal-only" by convention.** `off()` is a public
   `const fn` with no type-level restriction. Is "internal-only" enforced or just
   asserted in prose? Did this change improve or worsen the prior review's
   setup-engine footgun (an engine built with `off()` on which
   `run_shell_interactive` is still callable)?
4. **Over-structured policy type?** With only `{ Enforce, Off }` and `Off`
   internal-only, is `ShellSecurityPolicy { mode: ShellSecurityMode }` (a struct
   wrapping a two-variant enum) still earning its shape? Argue for one of: keep
   as-is (future modes), collapse to a bool, or drop `Off` and let the setup path
   express "no seccomp" another way.
5. **One-field `ManagerConfig`.** `ManagerConfig` now wraps a single
   `Option<DockerRuntimeConfig>`. Is the wrapper still justified? Is
   `#[serde(default, deny_unknown_fields)]` on a one-field struct a trap now that
   the daemon no longer reads it but the gateway does?
6. **Daemon/gateway config split.** The daemon stopped loading `manager`
   entirely; the gateway reads `manager.docker` (`gateway/main.rs:120`). Is the
   split clean, or did removing the daemon's `manager` load orphan a shared
   section in a way that will confuse the next reader of `prd.yml`?

## Axis 2 — No round-trips / no waste / no dead code

Probe and answer with evidence:

1. **No dead residue.** Prove the removal left nothing behind: no unused imports
   (`ShellSecurityMode`/`ManagerConfig`/`ConfigShellSecurityMode` in `serve.rs`),
   no orphaned `RELAXED_PROGRAMS`, no vestigial `mode` params, no now-unused
   derives (`PartialEq` on removed types), no dangling `#[serde(rename)]`. Back it
   with a clean `cargo build`, `cargo clippy --all-targets`, and a
   `cargo clippy --target aarch64-unknown-linux-musl -p
   sandbox-runtime-namespace-process --all-targets` (the enforcement module is
   Linux-gated).
2. **Is `Off` dead in practice?** `Off` is reachable only via the setup engine's
   `off()`. Is the `apply_shell_security_policy(Off)` branch (NNP + cap drop, no
   seccomp) actually exercised by any test or runtime path, or is it unverified
   surface that merely compiles?
3. **Filter is unchanged, not merely rebuilt.** Dropping the `mode` param made
   the namespace denials unconditional. Prove the emitted `enforce` filter is the
   **same** deny set as before (same families, `clone3` → `ENOSYS`, arch guard,
   X32 reject) — no denial silently added or dropped. The unit test asserts
   `build_seccomp_programs()` returns two filters; confirm it still covers the
   namespace rules without a `relaxed` counter-case.
4. **Per-construction cost.** `from_config` mints `enforce()` per call — confirm
   it is `Copy` and trivial, no allocation/lock. Confirm the command child path
   is otherwise untouched by the removal.
5. **Shared-file parse waste.** `prd.yml` still ships `manager.docker` (gateway)
   plus daemon/runtime/observability (daemon). Confirm the daemon cleanly skips
   `manager` now (no parse-and-discard) and the gateway still requires
   `manager.docker`. Any section parsed by a process that ignores it is a finding.

## Axis 3 — Security

This axis outranks the other two. A clean, dead-code-free removal that lets the
command child run anything but `enforce` is a failure.

1. **The core guarantee.** Prove the command child is **always** `enforce` with
   no config/env/default/constructor path to weaken it. Trace end-to-end:
   `from_config` → `CommandOperationService::new` → `NamespaceExecutionEngine::new`
   → `run_shell_interactive` → `NamespaceRunnerRequest.shell_security` → ns-runner
   `apply_shell_security_policy`. Name any link that could carry non-`enforce`.
2. **Downgrade via the retained param.** `CommandOperationService::new` accepts a
   policy. Is there any production caller other than `from_config`? Could a future
   caller (or a merged parallel branch) pass `off()` and silently disable seccomp
   on user commands? Rate this as a latent downgrade vector and prescribe the fix
   if it is one.
3. **Wire default still fail-closed.** Confirm `NamespaceRunnerRequest.shell_security`
   is still `#[serde(default)]` and `ShellSecurityPolicy`'s `Default` is still
   `enforce()` with `deny_unknown_fields` — so a dropped/garbage field across the
   ns-runner process boundary yields `enforce`, never `off`. The knob removal must
   not have changed this default.
4. **Relaxed fully gone, enforce intact.** Prove `build_errno_filter` now denies
   `setns`/`unshare`/`clone(NEW*)` **unconditionally** (no surviving `mode`
   guard). A half-removed `relaxed` that left the namespace denials off would be
   **Critical**. Confirm no code path builds a filter without the namespace rules.
5. **Default drift.** Confirm every default still resolves to `enforce`:
   `ShellSecurityPolicy::default()`, `ShellSecurityMode`'s `#[default]`, and the
   `from_config` literal. A change that made `off` the default anywhere is
   **Critical**.
6. **Shared-config misconfig behavior.** `prd.yml` is both the gateway config and
   the uploaded daemon config. With the knob gone: what happens if an operator
   ships a stale `manager.shell_security.mode: off` (or legacy
   `manager.command_security`)? The gateway parses `manager` with
   `deny_unknown_fields` (`gateway/main.rs:120`) — does it now **reject** at
   startup (fail-loud, good) while the daemon **ignores** `manager` (silent)? Is
   that split defensible? Confirm `prd.yml` and every shipped config carry no
   stale key.
7. **Setup-path containment (re-verify).** Setup engines still pass `off()` and
   must never exec user argv. Confirm the removal did not route any user command
   through `mount_overlay`/`remount_overlay`/`run_file_op`, and that
   `install_pgid_leader_hook` still only re-execs the runner, not user argv.

## Required output

Produce, in this order:

1. **Verdict table** — one row per axis (Architecture / Round-trips / Security),
   each `PASS | PASS-WITH-RISKS | FAIL`, with a one-line justification.
2. **Findings** — sorted by severity. Each: `[SEV] title` · claim · evidence
   (`path:line`) · concrete trigger · recommended fix.
3. **Top 3 must-fix** before this is considered done.
4. **What this change oversells** — any "removed"/"hardcoded"/"internal-only"/
   "fail-closed" claim not fully backed by the code.
5. **One question** you could not resolve from the code that a human must answer.

Do not soften. If it is clean, say so in one line and spend the rest on the
sharpest risk you can find anyway — the retained `new` param and the
gateway-rejects/daemon-ignores config split are the most promising places to dig.
