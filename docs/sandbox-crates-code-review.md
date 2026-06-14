# Code Review — `eos-sandbox/crates`

**Date:** 2026-06-13
**Scope:** `eos-sandbox/crates` (14 crates + `xtask`)
**Method:** Dynamic multi-agent workflow — 16 per-crate scouts → 4 cross-cutting specialists (architecture, naming, redundancy, auditability) → scored synthesis (21 agents). Every P0/P1 claim and every headline structural finding was independently re-verified against source; verified items are marked ✓.

---

## Architecture map (verified acyclic)

```
                 ┌─────────── leaves ───────────┐
   trace ────────┤  config        command(→config)
     │           └──────────────────────────────┘
     │     ┌──── mid tier ────┐
     │   layerstack ← overlay ← namespace(→config) ← plugin
     │                  │           │
     │              workspace ──────┘
     │     ┌──── orchestration ────┐
     ├──► operation (→8 crates) ──► daemon (→9) ──► eosd (bin)
     │
     └──► host (host-side; ONLY internal dep = trace)  ◄── wire ──┐
                    ▲                                              │
          gateway (→ host only) ── receive · gate · route · return┘
```

The host/engine separation is enforced **purely over the wire** — `host` imports only `trace`, never `daemon`/`operation`/`layerstack`/`overlay`/`namespace`/`plugin`. This is the workspace's strongest structural property.

| Layer | Crates | Role |
|-------|--------|------|
| Leaves | `trace`, `config`, `command`(→config) | proto codec / typed config / PTY+process |
| Mid | `layerstack`, `overlay`(→layerstack), `namespace`(→config,overlay), `plugin`(→namespace), `workspace`(→namespace,overlay) | substrate primitives, policy-free |
| Orchestration | `operation`(→8), `daemon`(→9), `eosd` (bin) | compose primitives into ops |
| Host side | `host`(→trace), `gateway`(→host) | durable audit warehouse / receive·gate·route·return |
| Tests | `e2e-test` | drives a live `eosd` over the wire |

---

## a. Scorecard

| # | Criterion | Score | One-line |
|---|-----------|:-----:|----------|
| 1 | Architecture simplicity | **8/10** | Acyclic, ownership-driven 14-crate split; minor dead deps & one oversized crate |
| 2 | Module isolation (SRP) | **7/10** | Leaves are pristine; orchestration/host tier has 5 files past the 800–1000 LOC smell line |
| 3 | Naming convention | **7/10** | Disciplined everywhere except `operation`'s 7× `lib.rs`-as-submodule break |
| 4 | Readability | **7/10** | Clear state machines & error taxonomies; dense codecs & zero docs on `trace` DTOs |
| 5 | Reuse / no redundancy | **6/10** | Big decisions right; copy-pasted micro-helpers + 3 untyped contract clones |
| 6 | Auditability & trace | **7/10** | Excellent happy path; **loss/backpressure edges leak silently** + dead crypto |
| | **Overall** | **~7.0** | Strong foundation, ship-quality happy path; close trace-loss gaps & dead scaffolding |

---

## Per-criterion detail

### 1 · Architecture simplicity — 8/10

**Good**
- Genuinely acyclic dependency graph; substrate primitives (PTY, overlay mount, layerstack CAS, namespace, plugin contracts) stay policy-free and orchestration composes them.
- Host/engine boundary enforced over the wire only — `host` depends solely on `trace`.
- Shared contracts reused, not recompiled: `host` imports the `trace` protobuf codec, `overlay` one-way re-exports `layerstack`'s `LayerChange` vocabulary, `ops.json` catalog is single-sourced.
- The feared "gateway duplicates wire types" problem does **not** exist — gateway delegates to `host`.

**Issues**
- `daemon` declares an unused `overlay` dependency ✓ (`daemon/Cargo.toml:27`; no `overlay::` in `daemon/src`, only `use overlay as _` in two test files).
- Wire frame limit (`MAX_REQUEST_BYTES`, 16 MB) and protocol version duplicated as independent literals across the unshared host↔daemon boundary.
- `operation` is one ~10.9k-LOC crate; its `plugin` submodule alone (~4.3k LOC: process lifecycle + PPC transport + manifest refresh + package publish + overlay exec) strains the single-crate choice.
- `gateway` embeds the op catalog via `include_str!("../../operation/ops.json")` ✓ (`gateway.rs:15`) — a relative-path reach into operation's tree that bypasses the dependency graph.
- Single-impl `Engine` delegation trait ✓ (`gateway.rs:131/157`) — needless indirection with no second implementation.

### 2 · Module isolation (SRP) — 7/10

**Good**
- Leaf/mid crates (`trace`, `command`, `overlay`, `plugin`, `namespace`) achieve near-perfect one-concept-per-file ownership, generally under ~500 LOC.
- Daemon's request→dispatch→adapter→envelope flow and runtime-service separation (`PluginRuntime`/`WorkspaceRuntime`/`invocation_registry`) are clean black boxes initialized once at startup.
- Trace emission decoupled from core logic via `FnMut` callbacks in `checkpoint/commit` (testable without trace infra).

**Issues** (all sizes verified)
- `host/src/host.rs` (1259 LOC) mixes registry lifecycle + forward/retry state machine + trace draining.
- `host/src/trace_store.rs` (1346 LOC) mixes sqlite schema + audit append + projection + query + reconciliation across 11 pub methods.
- `daemon/src/trace/sidecar.rs` (960 LOC) intertwines budget enforcement, span hierarchy, and event routing.
- `daemon/src/op_adapter/plugin_trace.rs` (544 LOC) is pure trace emission misplaced in the adapter layer (owns no domain logic).
- `operation/command/service.rs` (924 LOC) ↔ `finalize.rs` are bidirectionally coupled via `FinalizeCommandRequest`, a "pretend interface" request object whose fields `finalize.rs` only partially consumes.
- `daemon/runtime/workspace.rs` (586 LOC) bundles enter/exit + lease custody + recovery + eviction.

### 3 · Naming convention — 7/10

**Good**
- 9+ crates consistently apply thin `lib.rs` crate roots, correct `mod.rs` directory facades (layerstack, namespace, daemon, workspace), and precise domain vocabulary (`kernel_mount`, `yield_wait_loop`, `invocation_registry`, `BridgeAddressPool`, `VethAllocation`).
- No `utils`/`helpers`/`common` `.rs` file anywhere.
- Cargo crate directories correctly use kebab-case (`e2e-test`) while module paths stay snake_case.

**Issues**
- `operation` names all **7** directory sub-module facades `lib.rs` instead of `mod.rs`, wired via `#[path]` ✓ (`operation/src/lib.rs:3-19`). `lib.rs` is Cargo-reserved for the crate root; this is the canonical anti-pattern the repo naming rule guards against, repeated 7×. The `#[path]` attributes exist solely to preserve the non-idiomatic filename.
- `config/src/configs/e2e-test.rs` and `isolated-workspace.rs` (+ their 2 test counterparts) use kebab-case filenames with `#[path]` overrides restoring snake_case module names ✓ (`configs/mod.rs:9,12`) — a direct break of the snake_case module-file rule.
- `workspace/src/shared/` is the workspace's only vague bucket, holding four orthogonal concerns (capture, dirs, timing, tree).
- (e2e-test's kebab-case *test directories* like `workspace-runtime-command/` are Cargo `[[test]]` target dirs — rule-permitted, cosmetic only.)

### 4 · Readability — 7/10

**Good**
- Explicit state transitions throughout: command process lifecycle (`exit_taken` mutex, cancel-wins kill priority), layerstack `publish_layer` pipeline (validate→allocate→write→fsync→conflict→update), plugin service status machine.
- Clear error taxonomies with context: gateway's 8 named error kinds, `LayerStackError` naming expected/found versions, codec errors with full path context (`records[0].spans[1].kind`).
- Strong module-header invariant docs in `overlay` (atomic capture), `plugin` (MF-1 single-writer), `eosd` (exit-code contract).

**Issues**
- Netfilter/netlink codecs (`workspace/.../netfilter/wire.rs`, `exprs.rs`; `namespace/holder/network.rs`) use magic offsets (`IPV4_SADDR_OFFSET=12`, `NLMSG_HEADER_LEN=16`) with no packet-layout comments — correctness unverifiable without packet captures.
- Foundational `trace` crate has **zero `///` doc comments** on audit-critical DTOs (`TraceRecord`, `SpanRecord`, `DetailBudget`) and codec round-trip invariants.
- Dense high-cognitive-load logic: `transport.rs` callback routing (685 LOC), `checkpoint/commit.rs` git/overlay fallback without strategy comments, `run_recovery` 3-level match nesting.
- Large mixed files (`host.rs`, `trace_store.rs`, `sidecar.rs`) require scanning many private helpers to follow.

### 5 · Reuse / no redundancy — 6/10 *(lowest)*

**Good**
- `trace` is the sole owner of the protobuf codec and trace DTOs; `host`/`daemon` import `trace::TraceBatch`/`encode_trace_batch` rather than reimplementing serialization.
- Newline-JSON wire framing duplicated across daemon/host/plugin is **deliberate, documented isolation** (`plugin/wire.rs` states the rationale) guarded by e2e drift tests — justified load-bearing repetition, not careless copy-paste. **Do not merge.**
- Reusable fs/path primitives in `layerstack/fs.rs` and overlay change factories eliminate redundancy within those crates.

**Issues**
- Byte-identical micro-helpers copy-pasted across crate boundaries: `sha256_hex` ×3 ✓ (`host/host.rs`, `host/trace_store.rs`, `trace/budget.rs`), `usize_to_f64_saturating` ×5 ✓ (operation ×3, layerstack, daemon), `now_ms`/`unix_ms` ×4, `lock(&Mutex)` ×2 within the command crate. `trace` already owns reusable primitives (`string_id!` macro, digest helper) that downstream crates reimplement.
- Three cross-crate contract shapes reproduced as untyped data where an owned type exists:
  1. response-meta envelope hand-built as raw `json!` in gateway with `envelope_version:2` hardcoded twice (gateway has no `operation` dep);
  2. trace-sidecar constants + base64 framing byte-identical in `host/protocol.rs` and `daemon/trace/sidecar.rs`;
  3. error-kind wire vocabulary typed in `daemon/wire/message.rs` but bare string literals in gateway/host.
- Resource-timing key schema (`resource.cgroup.*`/`resource.process.*`) produced in `operation/finalize.rs` and re-parsed by ad-hoc `strip_prefix` in three daemon adapters; config validators duplicated intra-crate (`validate.rs` vs `e2e-test.rs`) and cross-crate (`plugin/service.rs`).

### 6 · Auditability & trace — 7/10

**Good**
- End-to-end ID correlation across all four hops (gateway→host→daemon→store): a single `trace_id`+`request_id` is minted at the gateway (`ForwardTraceContext::new`), threaded over the wire (`RequestTraceContext`), and stamped on gateway events, host transport/protocol events, the daemon sidecar span-tree, and the host SQLite warehouse (`trace_requests`/`spans`/`events`/`resources`/`links` + hash-chained `audit_entries`, WAL + `synchronous=FULL`).
- Schema is versioned and self-defending: proto3 `eos.trace.v1` with reserved-0 enum slots, committed golden round-trip test, decode-time subsystem derivation that refuses tampered wire bytes, store `user_version` newer-schema guard.
- Robust failure semantics: fail-closed mutations vs degraded reads (durable `trace_degraded` marker), `uncertain_outcome` loss records, host-reboot orphan reconciliation, bounded spool with budgeted truncation preserving `sha256` + `original_len`.

**Issues** (all ✓)
- **Daemon-counted `dropped_traces` is silently discarded at host ingest.** It is produced (`daemon/op_adapter/control.rs:107-121`, `trace/spool.rs`) and sent in `TraceBatch.dropped_traces`, but `ingest_trace_batch`/`project_trace_batch_tx` never read the field (zero references in `host/src`). Spool loss under backpressure — exactly the load condition — leaves no durable audit entry, no counter, and is not even detectable.
- **Background trace drain is tied to foreground traffic.** `TraceExportDrainer.schedule` has exactly one call site (`host.rs:535`, the forward `Ok` arm). No periodic timer, no drain on heartbeat/status. An idle sandbox running background commands can overflow the 4 MiB FIFO spool before the next foreground request — and that overflow is invisible (compounds the prior bug).
- `sandbox_heartbeats` table (with `spool_pending`/`spool_dropped_total`) is declared in DDL but **never INSERTed** (no `record_heartbeat` method).
- **ed25519 segment-sealing is fully dead scaffolding.** `ed25519-dalek` dep, `audit_segment_seals` DDL, `segment_id`/`key_id`/`signature` columns and three seal error variants all exist, but there is **no** `SigningKey`/`VerifyingKey`/`.sign()`/`.verify()` usage and no INSERT. The sha256 prev-hash chain is tamper-*detecting* but not tamper-*evident at rest* — an attacker with DB write access can recompute a consistent chain.

---

## b. What needs to be fixed

| Pri | Fix | Where | Action |
|:---:|-----|-------|--------|
| **P0** | Host discards daemon's `dropped_traces` → spool loss undetectable in durable store ✓ | `host/trace_store.rs:265,941`; producer `daemon/op_adapter/control.rs:107-121` | In `project_trace_batch_tx`, read `batch.dropped_traces`; when >0 write a durable loss/gap audit entry (mirror `record_response_missing`) with count + `daemon_boot_id`. |
| **P0** | ed25519 segment-sealing is dead scaffolding → chain not tamper-evident at rest ✓ | `host/Cargo.toml:11`, `host/trace_store.rs:40-46,1238` | **Decide one way:** implement (sign segment roots, INSERT seals, verify on read) **or** delete the dep, `audit_segment_seals` table, columns, and 3 seal error variants. Do not ship half-built. |
| **P1** | Trace drain tied to foreground traffic; no periodic fallback ✓ | `host/host.rs:535` (sole `schedule`), `:553-586` | Add a periodic/heartbeat-path host drain so background traces flush when idle; if reviving heartbeats, INSERT `sandbox_heartbeats(spool_pending, spool_dropped_total)`, else drop the dead table. |
| **P1** | `operation` uses `lib.rs` for 7 directory submodules ✓ | `operation/src/lib.rs:3-19` + 8 `*/lib.rs` | Rename each `<sub>/lib.rs` → `<sub>/mod.rs`, delete the `#[path]` attrs. Pure mechanical rename, no API change. |
| **P1** | 3 cross-crate contracts reproduced as untyped data ✓ | gateway response-meta vs `operation/core/envelope.rs`; host/daemon sidecar consts; error-kind vocab | Move sidecar field/schema/base64 constants into `trace` (both depend on it); define error-kind taxonomy as one enum in a shared leaf; expose a typed response-meta builder from `host`. |
| **P1** | Split 2× 1000+ LOC host files + misplaced `plugin_trace` | `host/host.rs` (1259), `host/trace_store.rs` (1346), `daemon/op_adapter/plugin_trace.rs` (544) | Split `host.rs` (registry / forward state machine / drainer); extract a `TraceQuery` from `trace_store.rs`; move `plugin_trace.rs` into `daemon/src/trace/`. |
| **P2** | Dead `daemon→overlay` dep + duplicated micro-helpers ✓ | `daemon/Cargo.toml:27`; `sha256_hex` ×3, `usize_to_f64_saturating` ×5 | Move overlay to `[dev-dependencies]` (or delete the `as _` test imports); export `sha256_hex` + epoch-ms helper from `trace` and import; collapse the 5 `usize_to_f64_saturating` copies. |
| **P2** | Kebab-case config files + `workspace/src/shared/` vague bucket ✓ | `config/src/configs/{e2e-test,isolated-workspace}.rs` (+tests); `workspace/src/shared/` | Rename to `e2e_test.rs`/`isolated_workspace.rs`, drop `#[path]`; split `shared/` into domain modules (keep capture/dirs/tree; inline timing). |
| **P2** | Remove gateway's single-impl `Engine` trait + `include_str` path-reach ✓ | `gateway.rs:131-207`, `:15` | Use `host::SandboxHost` directly; expose `ops.json` via a typed accessor on operation/host instead of a relative `include_str!` path. |

---

## Bottom line

A well-layered, ownership-driven workspace whose happy-path tracing is genuinely strong. Both P0s sit in the audit substrate — the one place silent gaps are unacceptable — so close the `dropped_traces` leak and resolve the dead crypto **before** treating this as a tamper-evident audit log. Everything else is localized, mechanical debt with clear, low-risk fixes.
