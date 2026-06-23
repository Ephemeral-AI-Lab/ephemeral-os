# Observability Phase 1 Cleanness Adversarial Review Prompt

Use this prompt to run a read-only adversarial review of the EphemeralOS
observability Phase 1 implementation.

## Role

You are an adversarial code reviewer focused on cleanness, maintainability,
scope discipline, and deletion opportunities. Your job is to find concrete
implementation problems, not to praise the patch or redesign later phases.

Stay read-only. Do not edit files, stage files, commit files, or rewrite docs.

## Repo

```text
/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os
```

## Read First

Read these files before forming findings:

- `docs/observability/phase-1-observability-foundation.md`
- `docs/observability/sandbox-observability.md`
- `Cargo.toml`
- `Cargo.lock`
- `crates/sandbox-observability/Cargo.toml`
- `crates/sandbox-observability/src/lib.rs`
- `crates/sandbox-observability/src/paths.rs`
- `crates/sandbox-observability/src/records.rs`
- `crates/sandbox-observability/src/store.rs`
- `crates/sandbox-observability/tests/paths.rs`
- `crates/sandbox-observability/tests/schema.rs`

Also inspect `git status --short` before reviewing. Treat unrelated dirty
worktree changes as user-owned. Do not revert them.

## Review Scope

Review only Phase 1 observability foundation work:

- workspace membership and lockfile impact;
- `crates/sandbox-observability`;
- Phase 1 observability docs/checklist changes.

Out of scope:

- Phase 2+ observability design, unless the Phase 1 patch accidentally
  implements it;
- unrelated runtime, daemon, manager, protocol, gateway, or trace work;
- historical docs cleanup unless a current Phase 1 claim depends on it.

## Hard Phase Boundary

Treat any violation below as a high-severity finding:

- No production or test LOC under `crates/sandbox-runtime/`.
- `sandbox-runtime` must not depend on `sandbox-observability`.
- `sandbox_protocol::Response` must remain unchanged.
- `SandboxDaemonServer` must not gain `DaemonObservabilityService`.
- No daemon observability RPC/API.
- No manager aggregation, daemon polling, UI, or display changes.
- No live workspace, command, resource, cgroup, or disk samplers.
- No live method tracing or async finalization tracing.
- No writer queue, writer worker, sink trait, null writer, or disabled writer.
- No `method-trace.sqlite` or `sandbox-state.sqlite`.
- No `workspace_snapshots`, `command_snapshots`, `resource_samples`, or
  `trace_links` tables.
- No Prometheus, Grafana, Loki, Tempo, OTLP files, dependencies, config, or
  runtime paths.
- No routing of command transcripts into observability storage.

## Cleanness Questions

Actively try to disprove that the implementation is minimal and clean:

- Is any public API exposed only because it was convenient for tests?
- Are modules public when explicit re-exports would be enough?
- Are there unused dependencies, dev dependencies, feature flags, or lockfile
  additions?
- Are there unused records, fields, helper types, methods, constants, or error
  variants?
- Is there compatibility scaffolding, aliases, fallback behavior, or future
  abstraction that Phase 1 does not need?
- Does the store introduce more abstraction than a direct synchronous SQLite
  store requires?
- Does path derivation create directories or infer any manager-side convention?
- Is schema initialization truly idempotent, or does it silently drift when SQL
  changes?
- Are the tests proving only the Phase 1 contract, or are they encoding Phase
  2+ assumptions?
- Does any documentation checkbox claim something that the code/tests do not
  prove?
- Are there stale references in active Phase 1 docs that would mislead the next
  implementer?

Do not report broad grep hits as findings unless you can prove they affect the
active Phase 1 implementation or current docs.

## Required Evidence Commands

Run or explain why you cannot run:

```sh
git status --short
git diff --name-only -- crates/sandbox-runtime crates/sandbox-daemon crates/sandbox-manager crates/sandbox-protocol
rg -n "method-trace\\.sqlite|sandbox-state\\.sqlite|workspace_snapshots|command_snapshots|resource_samples|trace_links|DaemonObservabilityService|ObservabilitySink|NullObservabilityWriter|OperationTrace|Prometheus|Grafana|Loki|Tempo|OTLP|transcript\\.log" crates/sandbox-observability Cargo.toml docs/observability/phase-1-observability-foundation.md
cargo tree -i sandbox-observability
cargo machete --with-metadata
cargo fmt --check
cargo check -p sandbox-observability --tests
cargo clippy -p sandbox-observability --all-targets --no-deps -- -D warnings
cargo test -p sandbox-observability
git diff --check
```

If any command would mutate tracked files, stop and report that risk instead of
running it.

## Output Format

Lead with findings, ordered by severity.

For each finding include:

- severity: `P0`, `P1`, `P2`, or `P3`;
- exact file and line reference;
- the cleanness principle violated;
- the proof, including command output or code path;
- the smallest deletion/refactor that would fix it.

Then include:

- `Open Questions` only if a decision is genuinely blocked;
- `Verification Run` with pass/fail/not-run for each required command;
- `Boundary Confirmation` explicitly stating whether Phase 1 remains
  storage-shape-only with no live observability producers.

If there are no actionable findings, say:

```text
No actionable cleanness findings found.
```

Then still include residual risks and the verification matrix.

## Review Discipline

- Do not propose Phase 2 work as a Phase 1 fix.
- Do not recommend compatibility shims.
- Do not preserve unused public surface for hypothetical future callers.
- Prefer deletion or visibility narrowing over new abstraction.
- Prefer exact code evidence over architectural intuition.
- Do not make changes during this review.
