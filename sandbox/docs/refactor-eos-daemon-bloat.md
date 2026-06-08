# eos-daemon Bloat Analysis & Refactor Plan

> Method: 11 parallel mapping agents (one per cohesive daemon area, paired with its
> candidate sibling crate), each classifying every chunk by **dependency-edge cost**
> — not topical affinity — then an adversarial verify pass that tried to *refute*
> every proposed move (back-edge / wire-contract / process-global-state checks),
> then a completeness critic. 21 agents total. Findings below were cross-checked
> against direct reads of `occ_callbacks.rs`, `file_ports.rs`, `manager.rs`, and the
> sibling `Cargo.toml` dependency DAG.

## TL;DR — separate the two readings of "bloated"

The complaint has two readings, and they come apart:

- **"This logic belongs in OTHER crates" (cross-crate extraction):** largely
  **refuted** — that extraction has already happened. Only ~6% of LOC is movable, and
  the moves that survive scrutiny are small. → Tiers 1–2.
- **"It's the biggest, hardest-to-navigate thing in the workspace" (internal size /
  organization):** **legitimate.** 51 files, five at 530–740 LOC, and six `services/*`
  sub-modules that each shadow a sibling-crate name make it *read* like a pile of
  reimplementations even though they are thin adapter/junction layers. → Tier 3.

If the concern is misplaced logic, the answer is "mostly already done, here are the 2
clean wins." If the concern is size and navigability, that is real and Tier 3
addresses it without any cross-crate move.

`eos-daemon` is large (≈10,927 LOC, 51 files) because it is **the control plane that
composes 14 of the 17 sibling crates** — not because it hoards their implementations.
The down-tier extraction the "misplaced code" reading predicts **has already
happened**:

| Domain logic the hypothesis expects to find in the daemon | Where it actually lives now |
|---|---|
| File read/write/edit algorithms (encoding, max-bytes, search-replace) | `eos-workspace-api::file_ops` + `eos-ephemeral/eos-isolated-workspace` |
| Git / pathspec / worktree commit logic | `eos-checkpoint-host` |
| Audit DTOs, lanes, schema, `build_event` | `eos-protocol::audit` |
| Isolated-run lifecycle (enter/exit/gc/ttl/persistence/network) | `eos-isolated-workspace` (3.5k LOC) |
| Overlay kernel-mount / path-change / writable-dirs | `eos-overlay` |
| OCC commit queue / route / single-writer service | `eos-occ` / `eos-occ-layerstack` |

Of **63 chunks across 11 areas, 54 were genuine glue that stays even pre-verify**;
only 9 chunks were proposed for a move. The adversarial pass then **downgraded 5 of
those 9 back to "stay"** (they would force a back-edge the codebase explicitly
forbids), leaving **59 effective "stay" and 4 surviving moves ≈670 / 10,927 LOC
(~6%)** — and 2 of those 4 need a new optional crate. Without that crate, only
**~70 LOC (<1%)** is cleanly relocatable.

What is left in the daemon is irreducible control-plane substrate: the AF_UNIX/TCP
RPC server, the op-table dispatcher + wire error-envelope, `DaemonError`
aggregation, three process-global single-writer singletons (OCC service cache,
isolated-session state, plugin process registry), the audit ring buffer + emission
bridge, and `ns-holder`/`ns-runner` child re-exec. None can move down without a
back-edge into `eos-daemon`.

## Architecture: why the glue is glue

```
                      RPC clients (AF_UNIX + 127.0.0.1 TCP)
                                   │  newline-delimited compact-JSON v1
                                   ▼
   ┌──────────────────────────── eos-daemon (control plane) ─────────────────────────────┐
   │  transport/server  →  dispatch/dispatcher (OpTable, error-envelope)                  │
   │  ops/* (thin RPC facades)   runtime/{DaemonError, invocation_registry, timings}      │
   │  ── DAEMON-RESIDENT SUBSTRATE (cannot move down without a back-edge) ──               │
   │  • OCC single-writer cache   OnceLock<Mutex<OccServiceCache>>   (services/occ)        │
   │  • isolated-session state    OnceLock<Mutex<IsolatedSession>>   (workspace_run/isolated)│
   │  • plugin process registry   OnceLock DaemonPluginState         (services/plugins)    │
   │  • audit ring buffer + emit  OnceLock<AuditBuffer> + safe_emit  (audit)               │
   │  • ns-holder / ns-runner re-exec via current_exe                                      │
   └───────────────┬───────────────┬───────────────┬───────────────┬─────────────────────┘
        composes…  │               │               │               │   (deps only point DOWN — DAG rooted at eos-protocol)
                   ▼               ▼               ▼               ▼
        eos-occ-layerstack   eos-plugin-host   eos-checkpoint-host   eos-{ephemeral,isolated}-workspace
        eos-occ              eos-plugin        eos-overlay           eos-command-session  eos-workspace-api
                                   └──────────────── eos-protocol (leaf) ────────────────┘
```

**The discriminating constraint** (verified against every sibling `Cargo.toml`):
`eos-ephemeral-workspace` and `eos-isolated-workspace` pull `eos-overlay` but
**deliberately exclude `eos-occ`** — `eos-isolated-workspace/Cargo.toml:14` says
verbatim *"eos-occ — that edge is the build-time no-publish guard and must stay
absent."* Any "move the write path down" instinct dies here: publishing must go
through the daemon's single OCC writer, so the publish-carrying code is pinned to
the daemon by design (the MF-1 single-writer invariant).

## Move-map: every non-trivial chunk, map verdict → adversarial verdict

| Chunk | Map verdict → Dest | Adversarial verdict | Why |
|---|---|---|---|
| `services/occ/mod.rs::base_hashes_for_snapshot` (~20 LOC) | move → `eos-occ-layerstack` | ✅ **move-confirmed** | Pure `MergedView`+`hash_current`; target already owns `hash_current`/`base_hash`; **zero new edges**; only `DaemonError` is cosmetic (returns native `LayerStackError`). |
| `services/checkpoint/base.rs` disk-walk (`count_dirs`/`storage_bytes`, ~50 LOC) | split-facade → `eos-layerstack` | ✅ **move-confirmed** | Walks `layers/` + `staging/` — the crate's *own* layout constants. Behind `LayerStack::storage_metrics()` DTO; **no new edge** (daemon already deps `eos-layerstack`). Facade stays to splice the OCC snapshot. |
| `workspace_run/registry.rs` `WorkspaceRunRegistry` (~355 LOC) | move → **NEW** `eos-workspace-run-host` | ⚠️ **move-needs-new-host-crate** | Survives DAG/contract/global-state (instance state, no static). Gated on first extracting `CommandHandle` out of daemon-only `isolated/runtime.rs`. Cannot land in thin `eos-command-session` (its doc assigns the registry to the composition tier). |
| `workspace_run/manager.rs` body (~250 LOC) | split-facade → **NEW** `eos-workspace-run-host` | ⚠️ **move-needs-new-host-crate** | Movable only if 3 seams stay **injected from the daemon**: `DaemonPublisherPort` (carries the forbidden `eos-occ` edge), the **audit emit** `record_tool_call(...)` at `manager.rs:542`, and `response_timings`. Most-constrained move; stage last. |
| `services/plugins/process.rs` env/spec + PPC bind/accept (~300 LOC) | split-facade → `eos-plugin-host` | ❌ **stay** (back-edge) | Transitive call-graph drags `spawn_overlay_runner` → `eos-runner`+`Intent` (overlay exec) and `nix::killpg` into a crate chartered to have *no* overlay/daemon edge; `PluginServiceProcess` is the daemon registry's value type. |
| `services/plugins/occ_callbacks.rs` (~180 LOC) | split-facade → `eos-plugin-host` | ❌ **stay** (back-edge) | Publish body reaches the daemon-global OCC writer (MF-1). The facade is *already* split correctly: host owns PPC transport, daemon injects the callback closure. Nothing left to move. |
| `services/plugins/ensure_args.rs` manifest derivation | split-facade → `eos-plugin-host` | ❌ **stay** (contract break) | Builds daemon-local `PluginOperationRoute`/`PluginProcessSpec` (which carry `eos-runner`); moving needs a neutral-DTO shim the repo forbids. *(Narrow exception: ~40 LOC of `resolved_service_command`/path math could move in a separately-scoped pass.)* |
| `workspace_run/isolated/runtime.rs` ns-runner spawn helpers (~150 LOC) | split-facade → NEW / `eos-command-session` | ❌ **stay** | **Zero external consumers** → a new crate would be a single-consumer abstraction CLAUDE.md forbids. Crate-map (`docs/contract/06`) explicitly mandates the daemon spawns holder/runner children. `eos-command-session` deliberately stays untyped (`&Value`) about the runner wire. |
| `services/workspace/file_ports.rs` ephemeral read (~25 LOC) | split-facade → NEW / `eos-occ-layerstack` | ❌ **stay** (orphan rule + contract) | `EphemeralFilePorts` carries a second `impl WorkspaceMutationSink` that stays in the daemon → moving the type out is an **orphan-rule compile error**. Read body also embeds daemon `/proc`+`/sys/fs/cgroup` telemetry via `resource_timings`. |
| `core` / `audit` / `ops` / `overlay` (remaining 53 chunks) | stay | ✅ stay | RPC framing, op routing, error-envelope, control plane, ring buffer + emit bridge, and the deliberate occ/overlay/plugin/workspace junction seams. |

## Recommended plan — three tiers, smallest-safe first

### Tier 1 — two edge-free wins ✅ DONE (verified: `cargo check`/`clippy -D warnings`/`test` green on all 3 crates)
1. **`base_hashes_for_snapshot` → `eos-occ-layerstack`.** Move the body next to the
   existing `hash_current`/`base_hash` it mirrors; have it return native
   `LayerStackError`. The daemon keeps a one-line re-export (matches `occ/mod.rs:19`'s
   existing re-export pattern) so the `DaemonError`-returning surface is preserved via
   `?`/`#[from]`. *Verify:* `cargo test -p eos-occ-layerstack -p eos-daemon`.
2. **`layer_metrics` disk-walk → `eos-layerstack::storage_metrics()`.** Move
   `count_dirs`/`storage_bytes` behind a typed `LayerStackMetrics` DTO; the
   `api.layer_metrics` facade stays in the daemon to splice in the OCC service-cache
   snapshot + GC placeholders and reassemble the **unchanged** wire JSON. *Verify:* the
   e2e tests that assert `manifest_depth`/`layer_dirs`/`storage_bytes` must still pass.

Combined impact: ~70 LOC leaves the daemon, two `layers/`/`staging/` literal leaks
close, zero new dependency edges. This is the entire "leaked logic" story without a
new crate.

### Tier 2 — optional: stand up `eos-workspace-run-host` (the only real "shrink the daemon" lever)
Mirrors the existing `eos-checkpoint-host` / `eos-plugin-host` pattern: a host-tier
crate that takes `eos-layerstack` + `eos-command-session` + `eos-ephemeral-workspace`
+ `eos-isolated-workspace` edges but **stays `eos-occ`-free** by keeping the publisher
injected. Net relocatable payload ≈ 600 LOC (`registry.rs` + the `manager.rs` body).
Only worth doing if you want the daemon materially smaller; otherwise skip.

Ordered preconditions (each is a real seam, not a rename):
1. **Extract `CommandHandle`** out of daemon-only `isolated/runtime.rs` (it embeds
   `WorkspaceRun::Isolated` and is used by 8 sites in `file_ports.rs` + `manager.rs`).
   Move it to the new crate or `eos-isolated-workspace` as plain data.
2. **Move `registry.rs`** (pure caller-keyed state container). The
   `OnceLock<WorkspaceRunManager>` singleton accessor stays in `commands.rs:42-45`.
3. **Move the `manager.rs` body** behind **three injected ports** the daemon keeps
   constructing: `WorkspacePublisherPort`/`DaemonPublisherPort` (the `eos-occ` edge),
   an **audit-sink recorder port** for `record_tool_call` at `manager.rs:542` (the
   audit area flagged that emission is daemon-global and must NOT travel down), and the
   timing/dir-allocator helpers. The daemon retains the wire/op facade
   (`commands.rs`/`wire.rs`) and the §7 cancel coordinator.

*Verify:* `cargo build -p eos-workspace-run-host` proves the no-publish guard holds
(it must compile with **no `eos-occ` dependency**); then `cargo test -p eos-daemon`
for the cancel-never-publishes and command-lifecycle behavior.

### Tier 3 — internal organization (this is what actually answers "bloated"; no cross-crate move)
This tier is the right one if the concern is size/navigability rather than misplaced
logic. None of it changes the dependency DAG.

- **Rename the `services/*` shadowing that creates the false "reimplementation"
  impression.** Six sub-modules — `services/{checkpoint,occ,overlay,plugins,workspace,
  workspace_run}` — each shadow a sibling-crate name, so the tree *looks* like a
  parallel reimplementation of those crates when it is actually the daemon-side
  adapter/junction layer. Renaming to signal intent (e.g. `adapters/` or per-area
  `*_bridge`/`*_host` suffixes like the existing `DaemonPublisherPort`) makes "this is
  the seam, not a copy" legible at a glance. Low-risk; pure module renames.
- **Fix the `response_timings` behavioral-divergence bug** the `core` analysis flagged:
  the daemon's saturating converter caps at `u32::MAX` while the same-named
  `eos-workspace-api` converter is uncapped, and both are reachable on the
  `*_tree_bytes` wire path. A latent correctness bug, not a move — fix or unify
  deliberately with a focused test.
- **Split the five oversized files along the concern boundaries the map identified**
  (cohesion, not relocation; per the repo's split-on-real-boundaries rule):
  - `plugins/process.rs` (738) mixes **four phases**: spec/env construction →
    PPC socket bind/accept → child spawn + overlay-runner re-exec → process-group
    teardown. Split into spec / ppc-socket / spawn-teardown.
  - `transport/server.rs` (541) mixes **transport framing** (read-line, TCP
    auth-strip, envelope decode) with **server lifecycle** (listener bind, reaper-task
    spawns, cancellation/shutdown). Split framing out of lifecycle.
  - `isolated/runtime.rs` (529) mixes the two **port impls** (`NamespaceRuntimePort`,
    `LayerStackSnapshotPort`) with ~150 LOC of low-level **ns-runner child-spawn / fd
    plumbing** (the chunk Tier-2 analysis showed must stay but is cohesively separable
    here).
  - `audit/events.rs` (531) is dispatch-response→event shaping; split per event family
    if it grows further (lower priority — it is mechanically cohesive today).
  - `workspace_run/manager.rs` (574) largely **leaves under Tier 2**; do not split it
    twice — sequence after the host-crate decision.

## What NOT to do (anti-recommendations the verify pass killed)
- ❌ Don't move `occ_callbacks.rs`, `overlay.rs`, or any write/publish path into
  `eos-plugin*` — forces the forbidden `eos-occ`/`eos-overlay` back-edge (MF-1).
- ❌ Don't move `process.rs` spawn core into `eos-plugin-host` — drags `eos-runner` +
  `nix` into a crate chartered to exclude them.
- ❌ Don't move `file_ports.rs` port impls into `eos-workspace-api` — that crate
  *deliberately* owns no daemon/LayerStack/OCC deps; the impls are the injected
  mechanics it refuses to host (and the read-path carve-out is an orphan-rule error).
- ❌ Don't create a crate just for the ns-runner spawn helpers — zero external
  consumers; that is the speculative single-consumer abstraction the guidelines reject.
