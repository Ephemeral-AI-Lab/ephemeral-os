# Refactoring Plan — `eos-sandbox/crates` remediation

**Source:** `docs/sandbox-crates-code-review.md` (P0/P1/P2 fix list)
**Date:** 2026-06-13
**Goal:** Land every review fix with maximum safe parallelism — phases gate on correctness, tracks within a phase run concurrently on **disjoint crate/file sets** so no two workers ever edit the same file.

---

## Parallelization model

- **Phase** = a sequential gate. Do not start Phase N+1 until Phase N's gate (build + test + clippy green, merged to `main`) passes.
- **Track** = a parallel lane *within* a phase. Each track owns a disjoint set of crates/files → safe concurrent editing. Run one agent/engineer per track, ideally in its own git worktree/branch.
- **Conflict rule:** two tasks may run in parallel ⟺ their file sets are disjoint. The contended files (`host/src/host.rs`, `host/src/trace_store.rs`) are confined to **one track per phase**.
- **Decisions first:** two human keep-or-kill calls (ed25519 sealing, heartbeat table) gate sub-tasks, so resolve them in Phase 0.

## Phase / track workflow

```
Phase 0  Decisions ───────────────────────────────────────────────┐
  D1 ed25519 seal: implement | delete                              │ gates T1.A scope
  D2 sandbox_heartbeats: wire | drop                               │
        │ (fast, ~0 LOC)                                           │
        ▼                                                          ▼
Phase 1  P0 correctness + isolated renames  ── 3 parallel tracks ──
  ┌──────────────┬─────────────────────────┬──────────────────────┐
  │ T1.A host    │ T1.B operation          │ T1.C config+workspace │
  │ trace-loss   │ lib.rs→mod.rs (×8)      │ kebab files + shared/ │
  │ P0-A P0-B P1A│ P1-B                    │ P2-B                  │
  └──────────────┴─────────────────────────┴──────────────────────┘
        │  gate: cargo build/test/clippy --workspace, merge
        ▼
Phase 2  Structural splits  ── 2 parallel tracks ──
  ┌────────────────────────────┬───────────────────────────────────┐
  │ T2.A host file splits      │ T2.B daemon plugin_trace relocate  │
  │ P1-D (host.rs, trace_store)│ P1-D (op_adapter→daemon/src/trace) │
  └────────────────────────────┴───────────────────────────────────┘
        │  gate: merge
        ▼
Phase 3  Shared contracts & dedup
  Step 3.0  PRODUCER (serial, single owner): add shared types/helpers to leaves
        │  gate: trace + accessors compile, merge
        ▼  ── 4 parallel consumer tracks ──
  ┌──────────┬──────────┬──────────┬───────────────────────┐
  │ T3.A gw  │ T3.B host│ T3.C dmn │ T3.D operation+layerstk│
  │ P1C P2C  │ P1C P2A  │ P1C P2A  │ P2A                    │
  └──────────┴──────────┴──────────┴───────────────────────┘
        │
        ▼
Final gate  workspace build + test + clippy -D warnings + xtask check-contract + e2e (Linux CI)
```

## Parallel-safety matrix (crate ownership per track)

| Phase | Track | Owns crates | Touches files | Disjoint from siblings? |
|-------|-------|-------------|---------------|:-----------------------:|
| 1 | T1.A | `host` | `host/src/{host,trace_store}.rs`, `host/Cargo.toml` | ✅ |
| 1 | T1.B | `operation` | `operation/src/lib.rs` + 8× `*/lib.rs`→`mod.rs` | ✅ |
| 1 | T1.C | `config`, `workspace` | `config/src/configs/*`, `workspace/src/shared/*` + lib | ✅ |
| 2 | T2.A | `host` | `host/src/host.rs`, `host/src/trace_store.rs` (split out) | ✅ |
| 2 | T2.B | `daemon` | `daemon/src/op_adapter/{plugin_trace,mod}.rs`, `daemon/src/trace/mod.rs` | ✅ |
| 3.0 | producer | `trace` (+ `operation/core/catalog`, `host` builder) | serial, single owner | n/a |
| 3 | T3.A | `gateway` | `gateway/src/gateway.rs` | ✅ |
| 3 | T3.B | `host` | `host/src/{protocol,trace_store}.rs` | ✅ |
| 3 | T3.C | `daemon` | `daemon/src/trace/sidecar.rs`, `daemon/src/wire/message.rs`, `daemon/Cargo.toml` | ✅ |
| 3 | T3.D | `operation`, `layerstack` | the 5 `usize_to_f64_saturating` copies | ✅ |

---

## Phase 0 — Decisions (gate, ~0 LOC)

| ID | Decision | Gates | Notes |
|----|----------|-------|-------|
| D1 | ed25519 segment-sealing: **implement** (sign roots, INSERT seals, verify on read) or **delete** (dep + `audit_segment_seals` + columns + 3 error variants) | T1.A ed25519 sub-task | Security posture call. "Delete" is the smaller, repo-rule-aligned default unless tamper-evidence-at-rest is required. |
| D2 | `sandbox_heartbeats` table: **wire** (add `record_heartbeat` INSERT with `spool_pending`/`spool_dropped_total`) or **drop** the dead DDL | T1.A drain/heartbeat sub-task | If P1-A adds a periodic drain, a heartbeat counter is the natural independent backpressure signal → lean "wire". |

---

## Phase 1 — P0 correctness + isolated renames (3 parallel tracks)

### T1.A — Host trace-loss hardening (`host`)
*Single owner of the two contended host files; covers all trace-loss correctness/security.*

| Item | Action | Files |
|------|--------|-------|
| **P0-A** | In `project_trace_batch_tx`, read `batch.dropped_traces`; when >0 write a durable loss/gap audit entry (mirror `record_response_missing`) with count + `daemon_boot_id` | `host/src/trace_store.rs:259,935` |
| **P1-A** | Add periodic/heartbeat-path host drain so background traces flush when idle (today: sole `schedule` site at `host.rs:557`) | `host/src/host.rs:526-586` |
| **P0-B** | Per **D1**: implement seal, or delete dep + `audit_segment_seals` + seal columns + `BadSegmentSignature` | `host/Cargo.toml:11`, `host/src/trace_store.rs:40-46,1232` |
| (D2) | Per **D2**: wire `sandbox_heartbeats` INSERT, or drop DDL | `host/src/trace_store.rs:1302-1321` |

**Verify:** `cargo test -p host`; add a regression test that a batch with `dropped_traces>0` produces a durable loss entry.

### T1.B — `operation` lib.rs → mod.rs (`operation`)
| Item | Action | Files |
|------|--------|-------|
| **P1-B** | Rename each `<sub>/lib.rs` → `<sub>/mod.rs`; delete the 8 `#[path]` attrs in `operation/src/lib.rs:3-19` | `operation/src/{core,checkpoint,command,control,file,isolation,plugin,workspace_run}/lib.rs`, `operation/src/lib.rs` |

**Ripple:** none — module paths (`operation::core`, …) are unchanged, so all daemon/eosd consumers compile as-is. Pure mechanical rename.
**Verify:** `cargo build -p operation -p daemon -p eosd`.

### T1.C — config + workspace naming (`config`, `workspace`)
| Item | Action | Files |
|------|--------|-------|
| **P2-B** (a) | Rename `e2e-test.rs`→`e2e_test.rs`, `isolated-workspace.rs`→`isolated_workspace.rs` (src + 2 test counterparts); drop `#[path]` in `configs/mod.rs:9,12` | `config/src/configs/*`, `config/tests/unit/configs/*` |
| **P2-B** (b) | Split `workspace/src/shared/` into domain modules (keep `capture`/`dirs`/`tree` as crate-root modules; inline `timing` at its single use) | `workspace/src/shared/*`, `workspace/src/lib.rs` |

**Ripple:** `workspace::shared` has no external consumers; config module paths unchanged. Both internal.
**Verify:** `cargo build -p config -p workspace` + downstream `cargo build -p namespace -p daemon`.

**Phase 1 gate:** `cargo build --workspace && cargo test --workspace && cargo clippy --workspace --all-targets -- -D warnings`. Merge all three tracks.

---

## Phase 2 — Structural splits (2 parallel tracks)

> Gated on Phase 1 so T2.A splits the host trace files **after** their content is final (avoids a brutal merge with T1.A).

### T2.A — Host file splits (`host`)
| Item | Action | Files |
|------|--------|-------|
| **P1-D** (a) | Split `host.rs` (1259) along its 3 concerns: registry lifecycle / forward+retry state machine / trace drainer | `host/src/host.rs` → `host/src/{registry,forward,drain}.rs` (or `host/` submodule) |
| **P1-D** (b) | Extract the projection+query concern from `trace_store.rs` (1346) into a `TraceQuery` type; leave append/reconciliation in `TraceStore` | `host/src/trace_store.rs` → `+ host/src/trace_query.rs` |

**Verify:** `cargo test -p host` (behavior-preserving; tests unchanged).

### T2.B — daemon `plugin_trace` relocation (`daemon`)
| Item | Action | Files |
|------|--------|-------|
| **P1-D** (c) | Move `op_adapter/plugin_trace.rs` (544, pure trace emission) into `daemon/src/trace/` as a trace helper | `daemon/src/op_adapter/{plugin_trace,mod}.rs`, `daemon/src/trace/mod.rs` |

**Verify:** `cargo test -p daemon`.

**Phase 2 gate:** workspace build + test + clippy. Merge both tracks.

---

## Phase 3 — Shared contracts & dedup (producer gate → 4 parallel consumers)

### Step 3.0 — Producer (serial, single owner)
Add the shared homes that consumer tracks will adopt. Keep it small; do not touch consumers yet.

| Adds | Home |
|------|------|
| `sha256_hex`, epoch-ms helper | `trace` crate |
| sidecar field/schema/base64 framing constants | `trace` crate |
| error-kind taxonomy enum (one set) | shared leaf (`trace` or small new home) |
| `ops.json` typed accessor (replaces gateway `include_str!`) | `protocol::catalog` |
| typed response-meta builder (`envelope_version`) | `host` (gateway already depends on host) |

**Verify:** `cargo build -p trace -p operation -p host`. **Merge before consumer tracks start.**

### Consumer tracks (parallel; each depends only on 3.0)

| Track | Crate | Items | Action |
|-------|-------|-------|--------|
| **T3.A** | `gateway` | P1-C, P2-C | Adopt response-meta builder + `ops.json` accessor; delete the single-impl `Engine` trait (`gateway.rs:131-207`) and `include_str!` (`:15`) |
| **T3.B** | `host` | P1-C, P2-A | Adopt sidecar consts + `sha256_hex` + error-kind enum from `trace` |
| **T3.C** | `daemon` | P1-C, P2-A | Adopt sidecar consts + error-kind enum; remove dead `overlay` dep (`Cargo.toml:27`); collapse local helpers |
| **T3.D** | `operation`, `layerstack` | P2-A | Collapse the 5 `usize_to_f64_saturating` copies into one home; import `trace` helpers |

**Verify (each):** `cargo test -p <crate>`.

---

## Final gate — workspace verification

```
cargo build --workspace
cargo test  --workspace
cargo clippy --workspace --all-targets -- -D warnings
cargo run -p xtask -- check-contract
cargo run -p xtask -- gen-docs            # if docs drift-checked
# e2e-test drives a live eosd over Linux namespaces → run on Linux CI, not darwin dev
```

---

## Execution discipline (multi-agent repo)

- One branch/worktree per track; rebase on `main` at each phase gate.
- A track's PR must be self-contained and pass its per-crate verify before the phase gate merges.
- P0 items (T1.A) are the long pole and the highest priority — they are correctness/security, not cleanup; they should not wait behind the mechanical renames even though they share Phase 1.
- Phases 2–3 are behavior-preserving; keep tests green before and after each split (no test rewrites except module-path imports).

---

## Progress tracker

Legend: ☐ not started · ◐ in progress · ☑ done · ⛔ blocked

| ID | Task | Review item | Phase | Track | Owns | Verify | Status |
|----|------|-------------|:-----:|:-----:|------|--------|:------:|
| D1 | ed25519 seal → **DELETE** | P0-B | 0 | — | host | decision recorded | ☑ |
| D2 | heartbeat table → **WIRE** | P1-A | 0 | — | host | decision recorded | ☑ |
| 1A-1 | Persist `dropped_traces` as durable loss entry (`_spool_overflow`, +2 tests) | P0-A | 1 | T1.A | host | `cargo test -p host` ✅ 23 lib + 5 contract | ☑ |
| 1A-2 | Periodic 15s host drain thread | P1-A | 1 | T1.A | host | `cargo test -p host` ✅ | ☑ |
| 1A-3 | Delete ed25519 scaffolding (table/columns/AuditAppend) | P0-B | 1 | T1.A | host | `cargo test -p host` ✅ | ☑ |
| 1A-4 | Wire `record_heartbeat` INSERT into drain | P1-A | 1 | T1.A | host | `cargo test -p host` ✅ | ☑ |
| 1B-1 | `operation` 8× lib.rs→mod.rs, drop 8 `#[path]` | P1-B | 1 | T1.B | operation | `cargo check -p operation` ✅ | ☑ |
| 1C-1 | Rename kebab config files + test wiring, drop `#[path]` | P2-B | 1 | T1.C | config | `cargo test -p config` ✅ 19 | ☑ |
| 1C-2 | Dissolve `workspace/src/shared/` → capture/dirs/tree, inline timing | P2-B | 1 | T1.C | workspace | `cargo test -p workspace` ✅ 11 | ☑ |
| — | **Phase 1 gate** | — | 1 | — | workspace | `check`+`clippy`(deny)+tests ✅, all green | ☑ |
| FU | Remove orphaned `ed25519-dalek` from root `[workspace.dependencies]` | P0-B | follow-up | — | root Cargo.toml | already removed by parallel work; 0 refs in tree | ☑ |
| 2A-1 | Split `host.rs` → `host/{mod,registry,forward,drain}.rs` | P1-D | 2 | T2.A | host | `cargo test -p host` ✅ 23 lib + 5 contract | ☑ |
| 2A-2 | Split `trace_store.rs` → `trace_store/{mod,query}.rs` (read methods delegate) | P1-D | 2 | T2.A | host | `cargo test -p host` ✅ | ☑ |
| 2B-1 | Move `plugin_trace.rs` → `daemon/src/trace/`, rewire imports | P1-D | 2 | T2.B | daemon | `cargo test -p daemon` ✅ 107 | ☑ |
| — | **Phase 2 gate** | — | 2 | — | workspace | `check`+`clippy`(deny) ✅ all green | ☑ |
| 3.0 | Producer: `trace::sha256_hex` (pub) + `trace::num::usize_to_f64_saturating` + `trace::sidecar::TRACE_SIDECAR_*` | P1-C,P2-A | 3 | producer | trace | `cargo check -p trace` + `--workspace` ✅ | ☑ |
| 3A-1 | gateway: adopt `protocol::catalog::BUILTIN_OPS`; remove direct `include_str!("../../operation/ops.json")` | P2-C | 3 | T3.A | gateway, protocol | `cargo test -p gateway` + `cargo run -p xtask -- check-contract` ✅ | ☑ |
| 3A-2 | response-meta envelope + protocol error-kind taxonomy live in shared `protocol` leaf | P1-C | 3 | T3.A/T3.C | protocol, gateway, daemon, operation | `cargo test -p protocol -p gateway -p daemon` ✅ | ☑ |
| 3B-1 | host: adopt `trace::sha256_hex` (−2 copies) + sidecar consts (e2e_support names preserved) | P1-C,P2-A | 3 | T3.B | host | `cargo test -p host` ✅ 23 lib + 5 contract | ☑ |
| 3C-1 | daemon: adopt sidecar consts + `usize_to_f64` (−1 copy) + drop dead `overlay` dev-dep | P1-C,P2-A | 3 | T3.C | daemon | `cargo test -p daemon` ✅ 107 | ☑ |
| 3D-1 | operation + layerstack: collapse `usize_to_f64_saturating` copies → `trace::` | P2-A | 3 | T3.D | operation, layerstack | `cargo test -p operation -p layerstack` ✅ | ☑ |
| FU | Remove clippy-denied legacy `Ok(expr?)` wrappers in E2E test joins/helpers and derive trivial config defaults | final-gate cleanup | follow-up | config, e2e-test | `cargo test -p config`, `cargo test -p e2e-test --no-run`, `clippy --workspace --all-targets -D warnings` ✅ | ☑ |
| — | **Phase 3 gate** | — | 3 | — | workspace | `check`+`clippy`(deny) ✅ all green | ☑ |
| — | **Final gate (macOS)** | — | 3 | — | workspace | `cargo check --workspace`, `cargo clippy --workspace --all-targets -- -D warnings`, `cargo machete --with-metadata`, `xtask check-contract`, `git diff --check` ✅; live e2e remains Linux/Docker-scoped | ☑ |

### Deferred — need a design decision (NOT auto-applied)

| ID | Item | Why deferred | Recommendation |
|----|------|--------------|----------------|
| 3A-x | gateway `Engine` trait removal (review P2-C) | **Review was wrong** — `gateway/tests/contract/mod.rs` has `impl Engine for {StubEngine, RecordingEngine}`. The trait is a **load-bearing test substitution seam**. | **Keep it.** Verified; no change. |
| — | host `trace_store/mod.rs` `TRACE_BATCH_SCHEMA` const | Same literal as `trace::TRACE_SIDECAR_SCHEMA` but a separate DB-projection concern; consolidating needs a naming decision in trace. | Low-value P2 polish; leave or fold later. |

**Critical path:** D1/D2 → T1.A → Phase-1 gate → T2.A → Phase-2 gate → 3.0 → T3.B → final gate.
T1.B, T1.C, T2.B, T3.A, T3.C, T3.D all run off the critical path and can finish early within their phase.
