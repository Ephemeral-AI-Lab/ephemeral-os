# Layerstack — Lease-State Cleanup + Observability Implementation Spec

> **Historical implementation specification (operation-layout exempt,
> 2026-07-11):** Package names and source paths below describe the tree in
> which this slice landed and are not current ownership guidance.

Status: archived after implementation.

This is the **implementation** spec for the layerstack slice of the observability
rework — the first vertical to ship (`README.md` "recommended order", reader-first).
It has two parts:

- **Part A — Lease-state cleanup** (`crates/sandbox-runtime/layerstack/src`): make
  `leases` the single source of truth and derive everything from it. This is a
  prerequisite, not cosmetic: today the registry caches a denormalized `refcounts`
  map and exposes `leased_layers()` (which actually returns the
  *mounted-by-any-lease* set, not only the layers a workspace directly leases) —
  building observability on that would mislabel by
  construction. Delete the cache; observability and cleanup both derive from `leases`.
  Vocabulary the views render: **leased by workspaces, booked by leased layers**.
- **Part B — Observability implementation**: a `LayerStack::observe()` method
  (lease state) + the leaf-crate `collect/layerstack.rs` reader (disk bytes) +
  daemon merge into the `layerstack` / `cgroup` / `snapshot` views.

Design source of truth: `README.md` (the rework model + recommended order) and
`cli-observability.md` (the CLI surface + rendered views). This spec says **what
changes in code**.

---

## 1. Glossary (canonical vocabulary ↔ code)

The active manifest is an ordered chain from base (`l0`) to newest (`ln`). A
workspace leases a layer — the newest in its manifest — and mounting it pulls in
every layer below.

| Term | Meaning | Source |
|---|---|---|
| **leased by workspaces** | count of workspaces whose lease targets this layer (the newest in their manifest) | derived from `leases` (each lease manifest's newest layer) |
| **booked by leased layers** | the leased layers above it whose mount pulls it in as a base | derived from `leases` + manifest order |
| **lease** | one workspace session's hold over a manifest | `Lease`, `LeaseRegistry`, `acquire_snapshot_with_lease` |

Both per-layer facts are computed on demand from the live `leases` — **no `needed`
concept, no `refcount` cache**.

---

## 2. Part A — Lease-state cleanup (`layerstack/src`)

### 2.1 `leases` as the single source of truth (delete the refcount cache)

The registry stores the same fact twice: `leases` (the records) and `refcounts` (a
denormalized `BTreeMap<LayerRef, usize>` rebuilt by `increment_layers` /
`decrement_layers` on every acquire/release). **Delete the cache** — `leases` is the
only source; every set is derived from it on demand. Nothing is *renamed* here, so
there's no half-renamed API to chase; the cache simply goes away (and with it the
sync-bug surface: a missed decrement → a layer "needed forever").

| Delete | Where | How callers re-derive from `leases` |
|---|---|---|
| `refcounts` field + `increment_layers` / `decrement_layers` (and the acquire/release calls to them) | `lease/registry.rs:19,66,73,90` | — |
| the `refcounts`-backed body of `leased_layers()` | `registry.rs:77`, `stack/mod.rs:96` | mounted-layer union from `leases`: `leases.values().flat_map(|l| &l.manifest.layers).collect::<BTreeSet<_>>()` |

`leased_layers()` now has a **single** consumer — `lease/cleanup.rs:30`
(`unreferenced_layers` keeps every layer any lease still mounts). Either keep the
accessor (re-derived from `leases`) or inline the union there; either way the
`refcounts` cache is gone.

Do **not** add per-layer accessors (leased-layer lists, per-layer counts) —
`observe()` (§3.1) computes the per-layer facts in one pass over `leases`.

Keep `active_lease_count()` (= `leases.len()`).

### 2.2 Bundled cleanups (aggressive simplification)

Done in the same pass — they remove derived/duplicated state (CLAUDE.md "fewer
fields") on the lease/snapshot types alongside §2.1.

**Survivor type.** `Lease` (`crate::stack`, already re-exported as `crate::Lease` at
`lib.rs:23`) is the single leased type; `LeasedSnapshot` is deleted and folded into
it. `Lease` must stay in `crate::stack`: `acquire_snapshot` (`stack/mod.rs:67`)
returns it, and the stack layer must not depend on `service` (where `LeasedSnapshot`
lives). The no-lease `Snapshot` (`service/model.rs:6-10`) stays a separate, lean
read view.

1. **Unify `Lease` + `LeasedSnapshot` → `Lease`.** Same data; they differ only in
   `layer_paths` — `Lease` holds `Vec<String>` (`stack/mod.rs:30`), `LeasedSnapshot`
   holds `Vec<PathBuf>` (`service/model.rs:18`), bridged by `snapshot_from_lease`
   (`service/support.rs:20-28`). The round-trip is **lossy at the `Vec<String>`
   end** — the strings come from `to_string_lossy` in `acquire_snapshot`
   (`stack/mod.rs:78`), not the `PathBuf::from` copy. Keep `Vec<PathBuf>`:

   ```rust
   pub struct Lease {                 // crate::stack
       pub lease_id: String,
       pub manifest: Manifest,
       pub layer_paths: Vec<PathBuf>,
   }
   ```

   Cascade:
   - Delete `LeasedSnapshot` (`service/model.rs:12-19`) and `snapshot_from_lease`
     (`service/support.rs:20-28`); drop the then-unused `use crate::Lease;` and
     `LeasedSnapshot` imports in `support.rs` — `snapshot_from_manifest` stays (it
     still builds the no-lease `Snapshot`).
   - `acquire_snapshot` (`stack/mod.rs:67-87`): build `Lease` with `Vec<PathBuf>`
     directly — drop the `.to_string_lossy()` map (`:78`) and the
     `manifest_version` / `root_hash` fields (`:82-83`).
   - `acquire_snapshot_with_lease`
     (`service/impls/acquire_snapshot_with_lease.rs`): return `Result<Lease, _>` and
     hand back `acquire_snapshot(request_id)?` directly — converter gone; drop its
     `LeasedSnapshot` / `snapshot_from_lease` imports.
   - `service/mod.rs:6`: `pub use model::{LeasedSnapshot, Snapshot};` →
     `pub use model::Snapshot;` (the leased type comes from `crate::Lease`).
   - `workspace` crate `model.rs:83-93`: `impl From<service::LeasedSnapshot> for
     LayerStackSnapshotRef` → `impl From<Lease>`, reading the two scalars via the
     accessors below (`:87-88`). `create_workspace.rs:19-27` needs no edit — it
     infers the type and already uses `.lease_id` + `.into()`.

2. **Drop the stored `manifest_version` + `root_hash` from `Lease`**
   (`stack/mod.rs:27-28`) — pure functions of `manifest`, so expose them as
   accessors, killing the lockstep re-sync (the drift hazard):

   ```rust
   impl Lease {
       pub fn manifest_version(&self) -> i64 { self.manifest.version }
       pub fn root_hash(&self) -> String { manifest_root_hash(&self.manifest) }
   }
   ```

   `root_hash()` recomputes per call; the sole caller (`workspace` `model.rs:88`,
   once per snapshot) makes that free. **Scope: `Lease` only.** The no-lease
   `Snapshot` (`service/model.rs:6-10`) has no `manifest` to derive from, and its
   consumers read the scalars as fields (`From<Snapshot>` at `workspace`
   `model.rs:44-45`; `latest_snapshot.rs:19`), so it **keeps** the two stored
   fields — that is what makes the `get_snapshot` path non-breaking.

### 2.3 Leave alone

- `release_lease` — unchanged. (`acquire_snapshot` / `acquire_snapshot_with_lease`
  keep their names and semantics, but per §2.2 return the unified `Lease` —
  `Vec<PathBuf>`, version/hash via accessors.)
- `owner_request_id` (the `acquire_snapshot` param, `stack/mod.rs:67`) — its
  **discard** is a separate finding (main spec §5: it can't be a trace id). The
  observability trace id is `Request.request_id`, not this.
- `active_lease_count()` (`stack/mod.rs:101`) — clear; keep.

---

## 3. Part B — Observability implementation

### 3.1 New layerstack API — `observe()` (lease state, in-memory)

A single read that returns the per-layer breakdown of the **active manifest**,
computed in one pass over the live `leases` + the manifest order. Pure runtime
state; no disk bytes (those come from the leaf reader, §3.2) — the daemon merges
the two.

```rust
// crates/sandbox-runtime/layerstack/src/service/  (new impl + model)
pub struct LayerStatus {
    pub layer: LayerRef,
    pub leased_by_workspaces: usize, // workspaces whose lease targets this layer
}

pub struct StackObservation {
    pub manifest_version: i64,
    pub root_hash: String,
    pub active_lease_count: usize,
    pub layers: Vec<LayerStatus>, // ordered base → newest
}

impl LayerStack { pub fn observe(&self) -> Result<StackObservation, LayerStackError> { … } }
```

Computation — one pass over the live `leases` (each lease manifest has a newest
layer), against the active manifest `[l0..ln]`:

- `leased_by_workspaces(li)` = number of leases whose newest layer is `li`.
- the booked-by relation (a layer is *booked by* the leased layers above it whose
  mount pulls it in as a base — the §1 rule) is a pure function of
  `leased_by_workspaces` + manifest order, so it is **not stored**; the daemon
  derives the ids at render (§3.3).

`manifest.layers` is stored **newest-first** — `layers[0]` is the newest layer and
the last element is the base (`stack/ops/publish.rs` prepends each new layer;
`projection` applies them via `.rev()`). `observe()` reverses storage so
`StackObservation.layers` is ordered base → newest, and "above `li`" then means
nearer the newest end.

No registry accessors, no `refcounts`, no `needed` — all of it falls out of one
pass over `leases`. `observe()` lives in layerstack because it needs the manifest
order; it returns layer **ids**, not bytes.

### 3.2 Leaf reader — `collect/layerstack.rs` (disk bytes, pure)

A pure `&Path → struct` in the observability leaf crate; disk bytes only.

```rust
// crates/sandbox-observability/src/collect/layerstack.rs
pub(crate) fn sample_layerstack(storage_root: &Path) -> LayerStackBytes
```

- Parse `storage_root/manifest.json` (`ACTIVE_MANIFEST_FILE`) for the ordered layer
  ids; per layer, read its size from the `.layer-metadata` sidecar
  (`LAYER_METADATA_DIR`), falling back to a budgeted du-walk of
  `storage_root/layers/<id>` (`disk.rs` budget) and repopulating the sidecar.
- **Sidecar write at publish:** record each published layer's byte size next to its
  digest (`fs.rs` `write_layer_digest` → add `write_layer_bytes`). Layers are
  immutable, so size is computed once; shared layers are sized once (keyed by id).
  This is the only layerstack-crate change Part B needs beyond `observe()`.
- Leaf-pure: depends on `std` + `serde_json` only; duplicates a minimal manifest
  deser struct rather than importing `layerstack` types (main spec §6 boundary).

### 3.3 Daemon merge → the views

`sandbox-daemon` joins `observe()` (lease state) with `sample_layerstack()` (bytes):

- **`layerstack` inventory view** (`cli-observability.md`): per-layer `bytes`
  (reader) + `leased by N ws` (from `observe()`) + `booked by <layer-ids>` (derived
  at render from the layer order + `leased_by_workspaces`; full ids, list capped
  with `+N more`). Header `N layers  <bytes>  K leases`. **Served live** — does not
  read the NDJSON log.
- **`stack` line in `snapshot`**: `N layers  <bytes>  K leases` + per-workspace rows
  (`mounts <n>  upper <bytes>`). Live.
- **periodic `stack` sample**: the daemon's `collect()` emits a `scope:"stack"`
  `sample` with `layer_count`, `layers_bytes`, `active_leases`. This is the only
  layerstack data written to the NDJSON log (for the `--window-ms` trend); needs the
  `Sink` from the obs crate skeleton.

The inventory needs **no** Sink or log; only the periodic sample does.

`layerstack` inventory render — layers base → newest; `booked by` lists the full
ids of the leased layers above it, capped with `+N more` when long:

```
4 layers   244KB   2 leases
  L000001-aaaa0000   120KB   leased by 0 ws   booked by L000003-cccc2222, L000004-dddd3333
  L000002-bbbb1111    84KB   leased by 0 ws   booked by L000003-cccc2222, L000004-dddd3333
  L000003-cccc2222    20KB   leased by 1 ws   booked by L000004-dddd3333
  L000004-dddd3333    20KB   leased by 1 ws   booked by —
```

### 3.4 Per-session view (`--workspace <ws>`)

For one session: the layers it mounts, and which other
workspaces share each (`shared with`), plus its private upper/workdir bytes. Mount
set = the session's lease manifest layers; `shared with` = other leases whose
manifest also contains the layer (the daemon joins the workspace registry to each
session's `Lease.manifest`). Upper/workdir bytes from the existing
per-`ws` disk sample.

### 3.5 Wiring (per `cli-observability.md`)

`get_observability` op gains `view:"layerstack"` (+ `cgroup`, `snapshot`); the CLI
gets `observability layerstack [--workspace] [--window-ms]`, `observability cgroup`,
`observability snapshot`. Add alongside the existing `get_observability_snapshot`
op — SQLite coexists until the main spec's removal phase.

---

## 4. Boundary

- The obs leaf crate gains `collect/layerstack.rs` (pure disk) — no `layerstack`
  dependency; the boundary test still holds.
- `observe()` returns **plain structs** of `LayerRef`/counts; the daemon (which
  already depends on both `sandbox-runtime` and the obs crate) merges. The runtime
  does **not** gain an obs-crate dependency in this slice (only the span phase
  does), so the boundary risk stays out of the layerstack-first work.

---

## 5. Rollout (layerstack-first, ordered)

1. **Lease single source** (§2.1) — delete `refcounts` +
   `increment_layers`/`decrement_layers`; `leased_layers()` re-derives from `leases`
   (sole consumer `cleanup.rs:30`); `cargo build`/`test` green. Behavior-preserving.
2. **`observe()`** (§3.1) — new method + `StackObservation`/`LayerStatus`; unit-test
   leased-by-workspaces / booked-by-leased-layers against fixture manifests + lease
   sets.
3. **Sidecar sizes + leaf reader** (§3.2) — `write_layer_bytes` at publish;
   `collect/layerstack.rs` in the obs crate.
4. **Daemon merge + views + CLI** (§3.3–§3.5) — `layerstack`/`cgroup`/`snapshot`
   views via the new `get_observability`; minimal obs-crate skeleton (`paths`,
   `Sample`, `Sink`, `Reader.samples()`) only where the periodic `stack` sample
   needs it. ← **layerstack observability ships here.**
5. *(later)* the span/trace half + layerstack **events** (publish/lease in a trace)
   — main spec Phases 3–6.

Steps 1–2 are layerstack-crate only; 3–4 add the obs reader + daemon wiring. No
throwaway — every piece carries into the full rework.

---

## 6. Testing

- **Lease single source:** `cargo build`/`clippy`/`test` green after deletion; grep
  `refcounts` → **zero** in `layerstack/src`; `cleanup` produces identical results
  from the inline derivation.
- **`observe()`:** fixtures — leases on `{l2, l3}` over `l0..l4` reproduce the §1
  rule: `booked by l0` = `booked by l1` = `{l2, l3}`, `booked by l2` = `{l3}`,
  `l4` leased by 0 ws / booked by — ; releasing the `l3` lease leaves `l3` (and
  `l4`) leased by 0 ws / booked by — (no lease needs them).
- **Reader:** sidecar sizes used without walking; missing sidecar falls back +
  repopulates; half-written `manifest.json` → skip, never panic; shared layer sized
  once.
- **Views:** `layerstack` inventory + `--workspace` + the `stack` snapshot line
  match the rendered examples; multi-session sharing shows `booked by` /
  `shared with` correctly.
- **Boundary:** obs leaf crate still has no `runtime`/`layerstack`/`daemon` dep.
