# Sandbox Architecture 7-to-9 Remediation Spec

Status: Draft
Date: 2026-06-14
Owner: `eos-sandbox`
Source review: senior architecture/code review on 2026-06-14
Target score: move the sandbox architecture from roughly **7/10** to **9/10**.

This spec is a remediation roadmap, not an implementation patch. It converts
the review findings into bounded phases with concrete acceptance gates.

## 1. Scope

In scope:

- `eos-sandbox/crates/{gateway,host,daemon,eosd,operation,protocol,trace,plugin,namespace,layerstack,config}`
- Trace and audit durability, redaction, lineage, and operator verification.
- Host-daemon and daemon-host operation wiring.
- Runtime ownership boundaries and public API width.
- Stale generated or historical documentation where it misleads active work.

Out of scope:

- Agent-core or backend-server rewrites.
- Replacing Docker as the sandbox provider.
- Reopening retired `api.*` wire vocabulary.
- A broad package rename unless explicitly split into a dedicated migration.
- Hand-editing generated inventory artifacts or E2E generated HTML.

## 2. Current Baseline

The review baseline is healthy enough to refactor from, but not yet audit-grade.

| Gate | Result |
|---|---|
| `cargo metadata --format-version=1 --no-deps` | Green; 16 workspace packages. |
| `cargo machete --with-metadata` | Green; no unused crate dependencies reported. |
| `cargo check --workspace --all-targets` | Green. |
| `cargo clippy --workspace --all-targets -- -D warnings` | Green. |
| `cargo run -p xtask -- check-contract` | Green; `ops.json` in sync and conformance suites passed. |

The architecture has a strong static operation catalog in `protocol::catalog`,
working host/daemon conformance checks, and an active trace store. The gap to
9/10 is not basic compilation health; it is correctness under degraded audit
paths, explicit runtime ownership, operational config wiring, and maintainable
module boundaries.

## 3. Target Architecture Principles

1. **Audit writes that prove mutating outcomes are load-bearing.** A mutating
   sandbox operation must not return ordinary success if the terminal audit
   state that proves its outcome cannot be persisted.
2. **Audit content is safe by construction.** Trace persistence must apply
   semantic redaction before size bounding. Byte budgets are not a redaction
   policy.
3. **Immutable audit entries and mutable projections stay separate.**
   `audit_entries` is the canonical record. Query tables, heartbeats, status
   views, and counters are projections unless explicitly audit-backed.
4. **Daemon hot paths stay local and bounded.** The daemon may emit sidecars and
   bounded in-memory trace batches, but it must not depend on host SQLite writes,
   fsync, or host RPC in the execution path.
5. **Runtime config is explicit.** Host-selected daemon config paths must be
   passed to the daemon as runtime input, not inferred from build-time paths.
6. **Operation policy has one source of truth.** Static built-ins come from
   `protocol::catalog`; dynamic plugin operations must have equivalent policy
   metadata or be formally constrained.
7. **Resource-owning services are instance-owned.** Long-lived daemon services
   such as command execution and layerstack state should be owned by runtime
   construction, not hidden behind process-wide globals.
8. **Public APIs are narrow.** Crates expose stable facades; low-level wire,
   persistence, and SQL internals remain private unless a real external consumer
   requires them.
9. **Large files split only on ownership boundaries.** Splits must improve
   lifecycle readability without creating thin abstraction churn.

## 4. Desired End State

| Area | Current score | Target score | Target condition |
|---|---:|---:|---|
| Structure and naming | 7.1 | 8.8 | Large mixed files split; stale docs gated; generated docs isolated. |
| Bugs, unused, legacy | 7.5 | 9.0 | No active docs describe retired paths; temporary deps have owners or removal gates. |
| SRP and coupling | 6.4 | 8.8 | Command/layerstack runtime state is instance-owned; host API narrowed; plugin contracts decoupled. |
| Trace and audit | 6.1 | 9.2 | Mutating outcome proof is durable; redaction is semantic; degraded/loss paths are explicit and testable. |
| Ops wiring | 7.0 | 9.0 | Config path, plugin op metadata, and host/daemon route metadata flow from typed sources. |

## 5. Phase Plan

### Phase 0 - Baseline And Stale Surface Freeze

Intent: make the current state repeatable before risky edits.

Tasks:

- Record the exact green baseline commands in the implementation PR.
- Classify `docs/contract/*` and bridge docs as historical or live.
- Add a stale-term check for active docs that rejects live references to retired
  wire names, old command vocabulary, old protocol-crate names, and
  Python backend source paths.
- Keep generated docs out of manual edits. Regenerate them only through their
  owning commands.
- Add missing exact test names for review-discovered filters that matched zero
  tests.

Acceptance:

- `cargo run -p xtask -- check-contract` remains green.
- Active docs either use current `sandbox.*` vocabulary or clearly declare
  themselves historical.
- No source code changes are required in this phase unless a check script or doc
  index needs ownership labels.

### Phase 1 - Audit Correctness First

Intent: close the highest-risk gap between a 7/10 and an audit-grade system.

Tasks:

- Make terminal audit writes load-bearing for mutating forwards:
  `response_persisted`, `response_missing`, and durable loss markers.
- If a post-forward terminal audit write fails, return an explicit
  `uncertain_outcome` or degraded response. Do not silently return ordinary
  success.
- Add a central redaction policy before `BoundedJson::capture` for request args,
  response summaries, trace event details, and sidecar resource payloads.
- Decide heartbeat semantics:
  - **Preferred:** append heartbeat samples and heartbeat loss/degraded markers
    to `audit_entries`, then project into `sandbox_heartbeats`.
  - **Allowed only if explicit:** document heartbeats as non-audit metrics and
    keep them out of `trace verify` claims.
- Add a bounded pending-sidecar recovery path for decoded trace batches that
  fail local ingestion.
- Refresh outgoing `meta.trace.event_count` from durable store state after
  `record_response_persisted`.

Acceptance:

- Injected SQLite failure after daemon response proves mutating operations do
  not return ordinary success without terminal audit evidence.
- Redaction tests cover keys matching `token`, `secret`, `password`, `api_key`,
  `auth`, `cookie`, plus known plugin and Docker secret shapes.
- `trace verify` either validates heartbeat audit entries or explicitly reports
  heartbeat data as a non-audit projection.
- `cargo test -p host trace_store` and focused host forward tests are green.

### Phase 2 - Operations Wiring And Policy Metadata

Intent: make operator behavior explicit and remove path/string drift.

Tasks:

- Add an explicit `eosd daemon --config-yaml` or `--config-dir` argument.
- Pass gateway `--remote-config` through host daemon startup so the daemon reads
  the same path the host copied.
- Add a non-default remote config E2E test.
- Stop reconstructing host audit family with `op.split('.')`; pass catalog
  family and mutability through the forwarding path.
- Replace host lifecycle string literals such as `sandbox.acquire` and
  `sandbox.release` with catalog-owned constants or typed contracts.
- Decide dynamic plugin operation policy:
  - Add a gateway-visible plugin metadata cache/query that includes visibility
    and mutability, or
  - formally constrain `plugin.*` to public-only and derive mutability from
    manifest `Intent`.

Acceptance:

- Non-default remote config path works in live Docker E2E.
- Gateway contract tests prove static public/operator/internal/test gating still
  works.
- Dynamic plugin op tests prove mutability and visibility are not hard-coded to
  public plus mutating unless that is the documented constraint.
- `cargo test -p gateway contract`, `cargo test -p host --test contract`, and
  `cargo test -p daemon --test contract` are green.

### Phase 3 - Runtime Ownership And API Width

Intent: move hidden process state behind explicit runtime ownership.

Tasks:

- Replace `command_ops() -> &'static CommandOps` with a daemon-owned command
  runtime stored under `RuntimeServices`, or make `CommandOps` hold shared
  config state that updates when daemon config is applied.
- Move layerstack service/lease registries behind an explicit runtime owner, or
  document the singleton as a process invariant with reset hooks for tests.
- Make `host::protocol` and `host::trace_store` private or crate-private.
  Re-export only stable facades and explicit `e2e_support`.
- Move plugin operation intent out of `namespace::protocol::Intent` and into a
  neutral contract DTO owned by `plugin` or `protocol`; convert to namespace
  intent inside operation/runtime code.
- Move duplicated host/daemon wire constants into the shared `protocol` crate
  when doing so does not reintroduce host/box compiled coupling beyond constants
  and DTO contracts.

Acceptance:

- A daemon server constructed with custom command config uses that config for
  new command execution without relying on initialization order.
- Tests that create multiple daemon/runtime instances cannot leak layerstack or
  command runtime state across cases.
- Downstream crates no longer import raw host trace-store write internals.
- `cargo check --workspace --all-targets` and `cargo clippy --workspace
  --all-targets -- -D warnings` are green.

### Phase 4 - Structural Splits

Intent: reduce maintenance risk after correctness and ownership are stable.

Split candidates:

| Current file | Split boundary |
|---|---|
| `crates/host/src/host/forward.rs` | `forward/request.rs`, `forward/recovery.rs`, `forward/trace_ingest.rs`, `forward/response_meta.rs`. |
| `crates/host/src/trace_store/mod.rs` | Keep root facade; move DTO/report/query-result types to `types.rs`; keep append/query/schema/audit modules separate. |
| `crates/daemon/src/trace/sidecar.rs` | `sidecar/build.rs`, `sidecar/events.rs`, `sidecar/resources.rs`, `sidecar/budget.rs`, `sidecar/transport_failure.rs`. |
| `crates/daemon/src/transport/server.rs` | Extract auth, decode, dispatch bridge, and lifecycle helpers once config-path changes are landed. |
| `crates/operation/src/plugin/transport.rs` | Separate PPC wire transport from plugin operation orchestration. |
Rules:

- Preserve public module paths through parent re-exports where needed.
- Do not split only to hit a line-count target.
- Move tests with the behavior they verify, but do not widen production APIs for
  tests.

Acceptance:

- Behavior-preserving refactor: no contract output changes unless explicitly
  planned.
- `cargo fmt --check`, `cargo test -p <touched-crate>`, and workspace clippy
  are green.
- Generated inventory is regenerated if crate/module topology changes.

### Phase 5 - Naming And Long-Term Hygiene

Intent: remove ambiguity that is not worth mixing into correctness phases.

Tasks:

- Decide whether generic package names such as `host`, `trace`, `protocol`, and
  `workspace` remain private-only or are renamed to `eos-sandbox-*`.
- If renaming, execute as one dedicated package migration with:
  workspace members, dependency keys, import paths, lockfile, generated docs,
  and stale-name scan.
- Keep `ConfigDocument` on the maintained `serde_yaml_ng` parser boundary; do
  not reintroduce the deprecated `serde_yaml` crate.
- Remove unused workspace dependency declarations such as `regex` and `toml`
  unless a live parser/test consumes them.

Acceptance:

- `cargo metadata --format-version=1 --no-deps` reflects the intended package
  graph.
- `cargo machete --with-metadata` stays clean.
- Active docs explain whether crate names are private implementation names or
  stable package names.

## 6. Verification Ladder

Use the narrowest useful gate first, then broaden only when the phase touches a
cross-crate contract.

| Change type | Minimum gate | Broader gate |
|---|---|---|
| Docs only | `git diff --check`; stale-term `rg` scan | `cargo run -p xtask -- check-contract` if API docs or contracts are touched. |
| Host trace/audit | `cargo test -p host trace_store`; focused host forward tests | `cargo check --workspace --all-targets`; live Docker trace E2E. |
| Operation catalog/routing | `cargo test -p protocol catalog`; gateway/daemon/host contract tests | `cargo run -p xtask -- check-contract`. |
| Config/daemon startup | Focused config and eosd tests | Live Docker E2E with non-default config path. |
| Runtime ownership | Focused daemon/operation tests | Workspace clippy with `-D warnings`. |
| File splits | Touched crate tests | Workspace check, clippy, generated inventory refresh. |

Standard full gate after a phase:

```sh
cargo fmt --check
cargo machete --with-metadata
cargo check --workspace --all-targets
cargo clippy --workspace --all-targets -- -D warnings
cargo run -p xtask -- check-contract
```

Add live Docker E2E for phases that alter daemon startup, host recovery, trace
drain, isolated workspace, plugin PPC, or config propagation.

## 7. Risk Register

| Risk | Why it matters | Mitigation |
|---|---|---|
| Audit terminal write failure after mutation | Can make a successful mutating response unprovable. | Fail closed or return `uncertain_outcome`; inject write failures in tests. |
| Over-redaction | Can make trace data useless for debugging. | Redact by key/path policy; keep sizes, hashes, and typed status fields. |
| Under-redaction | Can persist secrets in local audit DB. | Central redactor plus fixtures for common secret shapes. |
| Runtime singleton migration | Can destabilize tests and background tasks. | Move one service at a time; add instance-isolation tests. |
| Dynamic plugin policy | Can block plugin workflows if metadata discovery is too strict. | Start with public-only documented constraint or cache metadata after `plugin.ensure`. |
| Package renames | Can create broad churn and generated-doc drift. | Keep as a final dedicated phase after correctness work. |

## 8. Final 9/10 Definition

The sandbox reaches the target when all of the following are true:

- A mutating operation cannot silently lose terminal audit evidence.
- Trace persistence applies semantic redaction before storage.
- Heartbeat/status lineage is either audit-backed or clearly excluded from audit
  verification claims.
- Daemon config path flow is explicit and live-tested.
- Static and dynamic operation policy decisions are traceable to typed metadata.
- Command and layerstack runtime state are owned by runtime construction or
  documented process invariants with test reset hooks.
- Host public API exposes facades, not raw trace-store or wire internals.
- Large mixed files are split along real ownership boundaries.
- Active docs do not teach retired `api.*`, Python-era, or deleted-crate paths.
- The standard full gate plus relevant live Docker E2E is green.
