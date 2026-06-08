# eos-daemon Refactor Plan — Boundary Audit & Decomposition

> Method: 8 parallel module analyzers (told to **test, not confirm** the bloat claim)
> + 1 sibling-crate/DAG mapper → synthesis → adversarial challenge → finalize.
> Every load-bearing dependency fact below was re-verified by hand against
> `Cargo.toml` / `lib.rs` (grep-confirmed, noted inline).

## TL;DR — the premise is half-right, and the obvious fix is a trap

- **Your instinct is right that the crate mixes too many concerns** — ~12.5K LOC,
  one control plane fused with six host-adapter subtrees.
- **But "move the impls into the sibling modules" would break the architecture.**
  `services/*` is *not* code that belongs to the leaf crates. It is the daemon's
  **inverted-port implementation layer**: the leaf crates (`eos-plugin`,
  `eos-ephemeral-workspace`, `eos-workspace-api`, `eos-isolated-workspace`)
  deliberately own only *traits/contracts* and **refuse** `eos-occ` /
  `eos-layerstack` / `nix` / `tokio` / daemon edges. The daemon supplies the
  concrete impls precisely so those leaves stay contract-only. Pushing an adapter
  "into its same-named leaf" forces a **sibling → occ/layerstack/daemon back-edge**
  — a regression, not a cleanup. **8 of 8 slices ⇒ `keep-in-daemon`.**
- **The real decomposition that shrinks the crate without back-edges** is to
  extract **intermediate host crates** that sit *between* the leaves and the
  daemon (daemon → host-crate → leaves). Three candidates, below.

## Dependency DAG (verified)

```
                              eosd (bin)              ← only crate that depends on eos-daemon
                                  │
                          ┌───────▼────────┐
                          │   eos-daemon   │          ← SOLE composition root, depends on all 11 leaves
                          │  transport ·   │
                          │  dispatcher ·  │
                          │  InFlight reg ·│
                          │  audit ring ·  │
                          │  OccService$ · │
                          │  services/*    │  ← inverted-port impls (occ/layerstack-bound adapters)
                          └──┬──┬──┬──┬──┬──┘
        ┌──────────┬─────────┘  │  │  │  └──────────┬───────────┐
        ▼          ▼            ▼  ▼  ▼             ▼           ▼
   eos-runner  eos-cmd-     eos-isolated  eos-ephemeral  eos-config  eos-protocol
        │      session      -workspace    eos-occ ─┐                  (root leaf)
        ▼          ▼            ▼          eos-overlay │
   eos-overlay  eos-workspace-api          eos-plugin─┘
        └──────────┴────────────┴──────── all bottom out on eos-protocol; NO edge back to daemon
```

**Verified Cargo edges** (the facts the whole plan rests on):

| Crate | Internal deps | Consequence |
|---|---|---|
| `eos-occ` | `eos-protocol` **only** | cannot own layerstack-bound publish/route logic |
| `eos-layerstack` | `eos-protocol` **only** | moving `commit_to_git` here adds NEW `eos-overlay` + `uuid` edges ⇒ rejected |
| `eos-plugin` | `eos-protocol` **only** (comment in-file: *"NOT eos-occ — the no-publish/no-second-writer"*) | contract-only by design; host glue must NOT land here |
| `eos-command-session` | `eos-config`, `eos-workspace-api` | PTY substrate; folding `workspace_run` in forces occ/layerstack/ephemeral edges |
| `eos-ephemeral-workspace` | `eos-overlay`, `eos-workspace-api`, `eos-protocol` | owns traits, not occ; daemon implements `WorkspacePublisherPort` |
| `eos-isolated-workspace` | `eos-overlay`, `eos-workspace-api`, `eos-config`, `eos-protocol`, `nix` | comment in-file: *"NOT eos-occ — … no-publish guard … must stay absent"* |

## Verdict per slice

LOC analyzed ≈ 12,500: control-core 1497 · ops 829 · audit 920 · plugins 4423 ·
workspace_run 2708 · occ 873 · overlay/ws 749 · checkpoint 535.

| Slice | LOC | Verdict | Why it stays | Optional carve |
|---|---|---|---|---|
| **control-core** (server, dispatcher/OpTable, InFlightRegistry, error, request_args, response_timings) | 1497 | **keep** (essential) | this *is* the named control plane; `response_timings` fuses 5 crates, has no honest single home | — |
| **ops** (BUILTIN_OPS table, control/audit ops, service shims, files.rs) | 829 | **keep** (essential) | `Handler` ABI = `fn(&Value, DispatchContext) -> Result<_, DaemonError>`; moving `files.rs` drags `DispatchContext` into a contract crate | — |
| **audit** (ring buffer + emit bridge) | 920 | **keep** (essential) | DTO schema **already severed** into `eos_protocol::audit` (verified); ring + `events.rs` enrichment need the daemon ring + `eos-layerstack` | — |
| **services/plugins** | 4423 | **keep** (S) | `occ_callbacks`/overlay/process/live-registry are assigned to the daemon by `eos-plugin`'s MF-1 docstring; moving `occ_callbacks` forks the single writer | `eos-plugin-host` (M) — transport trio + `package.rs` |
| **services/workspace_run** | 2708 | **keep** (essential) | `manager.rs` constructs `DaemonPublisherPort` ⇒ transitive `eos-occ`; the PTY leaf forbids that | — |
| **services/occ** | 873 | **keep** (essential placement) | `service_cache.rs` is the §5 single-writer cache; `eos-occ` must not link `eos-layerstack` | `eos-occ-layerstack` (M) — `publish.rs` + `route.rs`, reuse only |
| **services/overlay + workspace** | 749 | **keep** (essential) | adapters implement leaf traits *inside* the daemon so leaves stay occ/layerstack-free; `run_ns_runner_child` mutates `InFlightRegistry` | — |
| **services/checkpoint** | 535 | **keep** (S) | not *misplaced* (occ-free, control-plane-free, sanctioned by `lib.rs`) — so any carve is a cohesion split, not a correctness fix | `eos-checkpoint-host` (M) — `commit_to_git` core; lowest-risk carve (Phase A) |

**Mandated relocations: zero.** The size concern is real, but it is a
**crate-cohesion** problem, not a **misplacement** problem.

## The five intentional-glue traps (where "push it into the leaf" is WRONG)

1. **Plugins facade (headline).** `eos-plugin`'s `Cargo.toml` forbids
   `eos-occ`/`eos-overlay`/`eos-layerstack`/`nix`/`tokio`, and its docstring
   assigns the live process registry, per-op overlay, and the self-managed OCC
   callback to **eos-daemon**. `occ_callbacks.rs` routes through the per-root
   single writer — moving it anywhere contract-only **forks the single writer**
   (correctness break). Keep.
2. **`manager.rs` → `DaemonPublisherPort`.** Folding `workspace_run` into
   `eos-command-session` forces that PTY-only crate to gain
   `eos-ephemeral-workspace` + `eos-isolated-workspace` + `eos-layerstack` +
   `eos-occ`. The port exists exactly to invert that edge out of the leaf. Keep.
3. **file_ports / overlay adapters** implement traits owned by
   `eos-workspace-api` / `eos-ephemeral-workspace`, crates documented as having
   *"no daemon, LayerStack, or OCC dependency."* Hosting the impls there is the
   precise back-edge the invariant forbids. Keep.
4. **occ `publish.rs` + `route.rs`** are layer-stack-bound; `eos-occ` deliberately
   does not link `eos-layerstack`. No contract sibling can structurally own them.
   Keep (optional binding crate, reuse only).
5. **`commit_to_git`** *looks* mechanically movable (occ-free, control-plane-free),
   but **`eos-layerstack` is the wrong target**: verified to depend on
   `eos-protocol` only, so the move adds NEW `eos-overlay` + `uuid` edges and dumps
   git-subprocess + overlay-mount glue into the no-tokio "storage truth" leaf.
   The right home is a NEW `eos-checkpoint-host` (daemon → host → leaves) — it is
   the lowest-risk cohesion carve, the recommended first step (Phase A).

## What stays in the daemon (the genuine control-plane core)

- **RPC server** (`transport/server.rs`) — AF_UNIX + loopback-TCP, capped/timed
  line read, pidfile, decode→dispatch→frame, reaper tasks.
- **Dispatcher / OpTable** (`dispatch/dispatcher.rs`, `ops/registry.rs`) — closed
  `HashMap<String, Handler>` with collision rejection, envelope validation, the
  `BUILTIN_OPS` golden table, and the dynamic-plugin fallback hook
  (`plugins::dispatch_registered_op`). `DispatchContext` is a `Copy` borrow bundle
  threaded end-to-end.
- **In-flight registry** (`runtime/invocation_registry.rs`) — invocation-keyed
  `killpg` SIGTERM→SIGKILL TTL reaper; the cancellation backstop a blocked
  `wait_with_output` cannot honor.
- **Audit ring buffer** (`audit/buffer.rs`) — bounded lane-priority-evicting ring,
  mutex never held across `.await`; backs `api.audit.{pull,snapshot}`.
- **Per-root OccService cache** (`services/occ/service_cache.rs`) — the §5
  dispatcher-owned single writer with the load-bearing **no-lock-across-await**
  drop ordering. Must never fork into two writers.

**Audit DTO question — resolved:** the portable schema (`Lane`, `*Section`,
`build_event`, `SCHEMA_VERSION`) **already lives in `eos_protocol::audit`**
(verified: `crates/eos-protocol/src/audit.rs`). No further DTO move is warranted.

## Recommended decomposition (if you want to shrink the crate)

**The driver is cohesion**, and it is legitimate: your bloat concern plus this
repo's own standard ("split when the file mixes multiple concepts… splits
following real ownership boundaries") both point the same way — a daemon that
fuses a control plane with six host-adapter subtrees is exactly that smell. So all
three carves below are warranted *under one uniform driver*; they are not gated on
hypothetical future reuse. The remedy is **host-crate extraction, not leaf
relocation**: each new crate sits *between* the leaves and the daemon and **gains
no dependency toward the daemon**.

Sequenced **risk-first** (safest move first, so each phase builds confidence):

### Phase 0 — lock the negative result (no code change) — **do this regardless**
Confirm nothing in `ops/` moves (keeps the `BUILTIN_OPS` golden test undisturbed)
and that the audit schema severing is complete.
*Verify:* `cargo test -p eos-daemon ops::registry`.

### Phase A — `eos-checkpoint-host` (~360 LOC) — **safest entry point**
Self-contained, occ-free, control-plane-free, **no precondition**. Move
`commit_to_git`'s core — pathspec policy, `PreparedWorktree` RAII
(overlay-vs-projection is a **closed runtime set ⇒ concrete struct, no dyn**), git
pipeline — into a crate depending on `eos-layerstack` + `eos-overlay` +
`eos-protocol` + `uuid` (verified: `commit.rs` imports only those). Replace the raw
`Value` return with a typed `CommitOutcome` DTO; the daemon op handler becomes a
thin adapter. `base.rs`, `layer_metrics`, and `commit_to_workspace` stay (they read
the OccService cache / stop plugin services).
**Add a response-shape golden test** for `api.v1.commit_to_git` (committed + no-op),
since the wire shape moves from a directly-built `Value` to a DTO re-mapped at the seam.
*Verify:* `cargo test -p eos-checkpoint-host` + `cargo test -p eos-daemon checkpoint`
+ `cargo clippy -p eos-checkpoint-host -p eos-daemon --all-targets -- -D warnings`.

### Phase B — `eos-occ-layerstack` (~700 LOC) — moderate, no correctness hazard
Extract `publish.rs` (`CommitTransactionPort` impl + validation/auto-squash) and
`route.rs` (`OccRouteProvider` + gitignore engine, pulls `ignore`) into a crate
depending on `eos-occ` + `eos-layerstack` + `eos-protocol` + `ignore` + `sha2`;
shed `DaemonError` for `OccError`/`LayerStackError`. `service_cache.rs` (the §5
single-writer cache) and timing shaping **stay** in the daemon.
Dispatch: keep `OccService<T: CommitTransactionPort>` as a **compile-time generic**;
`OccRouteProvider` as `Arc<dyn OccRouteProvider>` (object-safe, runtime-selected).
*Verify:* `cargo test -p eos-occ-layerstack` + `cargo test -p eos-daemon services::occ`;
OCC commit/conflict wire shapes unchanged.

### Phase C — `eos-plugin-host` (~1,050 LOC) — **highest LOC win, but riskiest; do last**
Last for a concrete reason, not size: it has a **hard precondition** and a
**correctness hazard**, where A and B have neither.
- **Precondition (must land first):** swap `DaemonError` → a local `PpcError` (or
  `eos_plugin::PluginError`) across `ppc_router.rs` + `pending.rs` + `frame_io.rs`
  (`pending.rs:10-12` bakes `DaemonError` into the `CallbackHandler`/`PpcResult`
  aliases; `ppc_router.rs:75` / `frame_io.rs:46` raise `DaemonError::StateLockPoisoned`).
  Until this swap lands, the carve *is* a sibling→daemon back-edge.
- **Hazard:** the OCC single writer must stay daemon-owned. **Keep** `occ_callbacks`,
  `overlay`, `process`, and the live registry in the daemon — these are the inverted-
  port glue (which is why the carve only reaches ~1,050 of the slice's 4,423 LOC).
Then move the **host-neutral PPC transport trio** (`ppc_router`/`pending`/`frame_io`)
+ `package.rs`. **Verified low coupling:** these four files reference only
`eos_plugin` + `eos_protocol` (+ `nix`, `std::process` for setup) — so the new crate
depends on just `eos-plugin` + `eos-protocol` (+ `nix`, `serde_json`, `sha2`), once
the error swap is done. The OCC writer is **injected as a `CallbackHandler` port** —
`Arc<dyn Fn(PpcEnvelope) -> Result<_, PpcError> + Send + Sync>` — never a second
`CommitQueue`.
*Verify:* error-swap compiles first; then `cargo test -p eos-plugin-host`
+ `cargo test -p eos-daemon services::plugins`; PPC frame round-trip golden unchanged.

## New crates — summary

| Phase | Crate | LOC | Owns | DAG placement (verified deps) | Dispatch strategy | Precondition |
|---|---|---|---|---|---|---|
| A | `eos-checkpoint-host` | ~360 | `commit_to_git` host logic (pathspec + worktree prep + git pipeline) | daemon → host → {eos-layerstack, eos-overlay, eos-protocol, uuid} | `PreparedWorktree` concrete RAII; `CommitOutcome` DTO | none — self-contained |
| B | `eos-occ-layerstack` | ~700 | layer-stack-bound `CommitTransactionPort` + `OccRouteProvider` | daemon → host → {eos-occ, eos-layerstack, eos-protocol, ignore, sha2} | `OccService<T>` generic; `Arc<dyn OccRouteProvider>` | shed `DaemonError` |
| C | `eos-plugin-host` | ~1,050 | host-neutral PPC transport trio + package publish/setup | daemon → host → {eos-plugin, eos-protocol, nix, serde_json, sha2} *(verified: moved files import only `eos_plugin`+`eos_protocol`)* | `CallbackHandler = Arc<dyn Fn(..)->Result<_,PpcError>+Send+Sync>` (injected); OCC writer stays daemon-owned | **`DaemonError`→`PpcError` swap first**, else back-edge |

All three fan in **under** the daemon; the DAG stays acyclic with `eos-daemon` as
the sole composition root and **zero leaf→daemon back-edges**.
