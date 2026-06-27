# Layerstack Observability — Side Spec

Status: ready-to-implement (additive to the main spec).

A companion to `README.md`. The main spec sources resource metrics from two pure
`&Path → struct` readers moved into the leaf crate (`collect/{cgroup,disk}`, §6),
and current entity state from the runtime's live `observability_snapshot()` (§4.2).
This side spec adds **layerstack** to both sides of that line:

- a third pure reader — `collect/layerstack.rs` — for the **structural** facts
  that live on disk (`manifest.json` + the `layers/` tree);
- a **pinned/leased** count drawn from the runtime, because that state lives only
  in process memory and is not on disk.

It changes **no** record kinds and adds **no** dependency to the leaf crate. It
reuses the main spec's `sample` kind (§3.3) and snapshot view (§4.2).

---

## 1. The on-disk / in-memory split (why the design is two-sided)

The cgroup reader is cheap because the kernel maintains those counters as
pseudo-files. The layerstack has no such kernel accounting — but most of what we
want is already persisted as plain files, and the rest is runtime memory.

| Layerstack fact | Lives in | Pure disk read? | Source |
|---|---|---|---|
| layer count, ordered refs, revision, root hash | `storage_root/manifest.json` | ✅ one read + parse | `lib.rs:36` (`ACTIVE_MANIFEST_FILE`), `storage/fs.rs:189` (`read_manifest`) |
| per-layer directory + bytes | `storage_root/layers/<id>` | ✅ du-walk (cached, §6) | `stack/mod.rs:8` (`LAYERS_DIR`), `:85` (`resolve_layer_path`) |
| active upperdir bytes | the workspace upperdir | ✅ already done | `disk.rs:24` (`sample_upperdir`) |
| **per-layer refcount / pinned set / lease count** | **process memory** | ❌ never persisted | `lease/registry.rs:18` (`refcounts: BTreeMap<LayerRef,usize>`), `:139` (process-global `OnceLock`), `stack/mod.rs:116` (`leased_layers`), `:121` (`active_count`) |
| protected pinned bytes | computed by reclaim | ❌ runtime call | `reclaim_unpinned_layers/mod.rs:56` (`protected_pinned_bytes`) |

**Sharing model (load-bearing — get this right).** There is **one** `LayerStack`
per sandbox (`storage_root`), shared by every workspace session; the lease
registry is keyed by the stack root (`shared_registry_for_root`, `registry.rs`).
Each session takes its **own** lease over a manifest (its lower layers) and has
its **own** upperdir / workdir / mount namespace — but **lower layers are
shared**: the same layer can be leased by many sessions at once. The registry
tracks this as a **per-layer refcount** (`refcounts: BTreeMap<LayerRef,usize>`,
`registry.rs:18`; `increment_layers`/`decrement_layers` on acquire/release). A
layer is *pinned* iff refcount ≥ 1; `leased_layers()` returns the **union** of
pinned layers across all leases, **not** a per-owner list — owners are not stored
(`owner_request_id` is discarded, main spec §5). So:

- **Pinned-ness is a stack-global refcount**, not a per-workspace fact.
- **Bytes of lower layers belong to the stack, counted once.** Summing them
  per-session double-counts every shared layer.
- **Only upperdir/workdir/namespace are session-local.**

This dictates the scoping: `scope:"stack"` carries the shared inventory (per-layer
bytes once + refcounts); `scope:"<ws>"` carries only the session's **private**
bytes (its upperdir — the existing disk sample — and workdir) plus *which* layers
its lease references (ids, not bytes).

**Rule of thumb:** structure → disk reader (leaf crate). Pins → runtime
(`observability_snapshot()`), exactly like in-flight executions in the main spec.
Do **not** reconstruct pins by folding `lease.acquired` − `lease.released` from
the log: it breaks on daemon restart (the in-memory registry resets to empty
while the log still shows old acquires) and on crash (a `lease.acquired` with no
release → false "pinned forever"). The in-memory registry is the only truth.

---

## 2. The reader — `collect/layerstack.rs`

A pure function, the same shape as `cgroup.rs` / `disk.rs`, owned by the leaf
crate (no `runtime`/`layerstack` dependency — it reads files, not types):

```rust
// crates/sandbox-observability/src/collect/layerstack.rs
pub(crate) fn sample_layerstack(storage_root: &Path) -> LayerStackSample
```

What it does:

1. Read `storage_root/manifest.json` → `layer_count`, ordered layer ids,
   `revision`, `root_hash` (parse the same JSON the runtime writes — see §7).
2. For each layer id, resolve `storage_root/layers/<id>` and obtain its size
   (cached, not re-walked — see §6).
3. Sum to `layers_bytes`; emit `truncated:true` if a walk hit the node/depth
   budget (reuse `disk.rs`'s budget).

It does **not** know about leases — pins are merged in by the daemon, which has
the runtime handle (§4.1). The reader stays leaf-pure.

---

## 3. `/sys/fs` and `/proc` — what the kernel does and doesn't give us

The natural question is whether the kernel exposes this like `/sys/fs/cgroup`.

- **cgroup (`/sys/fs/cgroup`) — yes, and there's a free win.** `cgroup.rs`
  already reads `cpu.stat`, `memory.current`, `memory.max`. Add **`io.stat`**
  (per-cgroup block-I/O `rbytes`/`wbytes`) — a real kernel counter, cheap, and
  useful for "is this workspace hammering disk." See §4.3.
- **overlay / layer sizes — no.** There is **no `/sys/fs/overlay` byte
  accounting**; overlayfs exposes nothing in sysfs for per-layer or upperdir
  sizes. The only kernel-exposed overlay source is **`/proc/self/mountinfo`**:
  for an overlay mount the super-options carry `lowerdir=a:b:c`, `upperdir=…`,
  `workdir=…`, so you can *discover paths and count lowerdirs* without runtime
  state — but it yields **paths, not bytes**.
- **cheap bytes would need a layout change.** O(1) used-bytes is only possible if
  the upperdir sits on its own quota'd mount / loopback (`statvfs` / project
  quota). By default it's a subdir, so bytes require a walk. We take the walk and
  make it cheap by caching (§6) rather than changing the storage layout.

Net: `/sys/fs/cgroup` is the right source for CPU/mem/**io**; for layer sizes the
walk is unavoidable, and `/proc/self/mountinfo` is the cheap source for overlay
*structure* if we ever want lowerdir discovery without the manifest.

---

## 4. Record & view additions

### 4.1 Periodic `sample` — aggregate stack metrics (one shared stack)

A `sample` (main spec §3.3) with `scope` = `"stack"` (one stack per sandbox; use
`"stack:<id>"` only if a sandbox ever holds several). The daemon's `collect()`
calls `sample_layerstack(storage_root)` (disk: unique-layer count + bytes) and
merges lease-derived fields from the live registry (`leased_layers()` →
`leased_layers`, `active_count()` → `active_leases`, `protected_pinned_bytes` →
`protected_bytes`) before emitting:

```json
{"ts":1719500010000,"kind":"sample","sandbox":"eos-abc","component":"sandbox-daemon","scope":"stack","layer_count":4,"layers_bytes":2516582,"leased_layers":3,"unleased_layers":1,"leased_bytes":2360022,"freeable_bytes":156560,"active_leases":2,"revision":"r6","truncated":false}
```

`layers_bytes` counts each unique layer **once** (never per-session);
`leased_layers` = layers with lease count ≥ 1; `unleased_layers` = those with
zero; `freeable_bytes` = unleased & unprotected bytes you can reclaim;
`active_leases` = number of live sessions holding a lease. Bounded and flat (no
per-layer array — that would blow the line cap, main spec §6). It answers "is the
stack growing / are leases/pins leaking over time" via the Case D delta
machinery. **Per-session private bytes stay the existing `scope:"<ws>"` disk
sample** (upperdir; add workdir if wanted) — that's where the per-workspace
numbers live, with no double-counting of shared layers.

**Rendered — `observability layerstack --samples` (main spec §7.5):**

```console
$ sandbox-cli observability layerstack --sandbox-id eos-abc --samples --window 60000
scope stack   window 60s   (Δ computed at read)

  t(+s)   layers   Δlayers   unique_bytes   Δbytes     leased   leases
  00.0      4         –         2.40MB         –          3        2
  60.0      5        +1         2.88MB      +480KB        4        2
```

### 4.2 On-demand per-layer detail — `layerstack`

Per-layer breakdown is high-cardinality, so it is **not** logged periodically — it
is served fresh on request, joining the disk reader (sizes), `manifest.json`
(ordering/revision), and the runtime registry (**lease counts**). Same composition as
the snapshot view (main spec §4.2): served by the daemon, not folded from the log.
Two modes — stack-wide inventory, and one session's view.

**Stack-wide inventory** (the shared store):

```console
$ sandbox-cli observability layerstack --sandbox-id eos-abc
stack r6   4 layers (3 leased, 1 unleased)   2.40MB   156KB freeable   2 leases

  layer        bytes    leases   status
  l0 (base)    1.80MB     2
  l1           480KB      2
  l2            80KB      1
  l3           156KB      0      freeable
```

`leases` = how many live sessions hold the layer (from the registry); `status` is
blank when leased, `freeable` when no lease holds it, `superseded` when a newer
revision replaced it. Bytes are the unique per-layer size (cache §6). There is
**no owner column** — owners aren't stored, only the count.

**Per session** (`view:"layerstack"`, `workspace:"ws-7"`): the layers *this*
session's lease references (shared, with stack-wide lease count) plus its **private**
upper/workdir:

```console
$ sandbox-cli observability layerstack --sandbox-id eos-abc --workspace ws-7
workspace ws-7   lease over r5   3 lower layers (shared)   upper 156KB   workdir 8KB

  lower (shared — bytes belong to the stack)
    l0 (base)  1.80MB   2 leases
    l1         480KB    2 leases
    l2          80KB    1 lease
  upper (private, live)
    156KB   (writable)
```

The lower layers are shared (note the lease counts > 1 — `ws-9` leases l0/l1 too);
only `upper`/`workdir` are this session's own bytes (from the `disk.rs` walk).

### 4.3 cgroup `io.stat` (bonus, main spec `cgroup.rs`)

Extend the cgroup sample with two fields read from `/sys/fs/cgroup/<scope>/io.stat`:

```json
{"ts":...,"kind":"sample","sandbox":"eos-abc","component":"sandbox-daemon","scope":"ws-7","cpu_usec":4100000,"mem_cur":21000000,"mem_max":268435456,"io_rbytes":1048576,"io_wbytes":4194304,"disk_bytes":1320000,"files":340}
```

Deltas computed at read like every other counter (main spec §4.4).

### 4.4 Snapshot view addition (main spec §4.2)

Leased/freeable counts are **stack-global**, so they get their own line in the
`observability` snapshot (main spec §7.1); the per-workspace rows show only what's
session-local (leased lower count + private upper bytes). Both come from the live
registry + disk reader:

```console
$ sandbox-cli observability snapshot --sandbox-id eos-abc
sandbox eos-abc   state ready

  stack   r6   4 layers (3 leased, 1 unleased)   2.40MB   156KB freeable   2 leases

  workspaces
    ws-7   active   profile=default   lower=3 (shared)   upper 156KB
    ws-9   active   profile=default   lower=3 (shared)   upper  88KB
```

Note `ws-7` and `ws-9` both report `lower=3` over the same shared layers — the
2.40MB is counted once at the stack line, not added per workspace.

---

## 5. Caching immutable layers (making the walk cheap)

Published layers are **immutable** (CAS-style; a published revision never
mutates), so their byte size is computed **once** and never recomputed:

- At publish, record the layer's total bytes into the existing per-layer metadata
  sidecar (`.layer-metadata`, `publish/route.rs:42`; alongside the layer digest
  written by `write_layer_digest`).
- `sample_layerstack` reads sizes from the sidecar and **sums** — no walk for
  published layers.
- Sizes are keyed by **layer id**, so a layer shared across N sessions (§1) is
  measured **once**, not N times — the cache and the refcount model line up.
- Only the **active upperdir** is mutable and is walked live (per session) —
  already handled and throttled (10s min interval) by `disk.rs`.

Result: the layerstack sample is ~O(layer_count) sidecar reads in steady state,
not an O(tree) du. A missing/legacy sidecar falls back to a one-time walk that
then populates the sidecar.

---

## 6. Consistency caveat (atomic read)

`cgroup.rs` is trivially safe because the kernel serves complete pseudo-files.
`manifest.json` is a real file a publisher rewrites, so the reader must not catch
a torn write. The runtime already takes a `StorageWriterLockLease`
(`stack/mod.rs:53`) for writes; ensure manifest writes are **write-temp + rename**
(atomic on the same fs) so any external reader sees a complete file without
needing the writer lock. `sample_layerstack` then reads lock-free and tolerates a
read landing on revision N or N+1 (eventual consistency is fine for a sample). If
a parse fails (mid-rename race or corruption), emit `truncated:true` and skip the
sample rather than fail collection (main spec: observability never fails the op).

---

## 7. Boundary

`collect/layerstack.rs` reads files and returns a plain struct — it depends on
nothing but `std` + `serde_json` (to parse the manifest payload shape it already
knows). It does **not** import `sandbox-runtime` / `layerstack` types, so the leaf
stays a leaf (main spec §6 boundary). The pinned merge happens in the daemon,
which already holds both the reader output and the runtime handle — no new edge
into the leaf crate. The manifest JSON shape is duplicated as a minimal
deserialization struct in the leaf (just the fields it needs: layer ids,
revision), not shared from `layerstack`, to avoid a dependency edge.

---

## 8. Rollout (additive to main spec §9)

Slots into the main spec's phases; nothing here blocks Phase A.

1. **Reader** — `collect/layerstack.rs` (manifest parse + sidecar sizes + budgeted
   fallback walk). Standalone, unit-tested with fixture stacks.
2. **Publish sidecar** — write per-layer bytes into `.layer-metadata` at publish
   (layerstack change; one number alongside the existing digest).
3. **Daemon merge** — `collect()` emits the `stack:<ws>` sample (reader + registry
   pins); extend `cgroup.rs` with `io.stat`.
4. **Views** — add `view:"layerstack"` to `get_observability` + the
   `observability layerstack` CLI subcommand (`--workspace`, `--samples`); add
   pinned counts to the snapshot view rows; surface `io.stat` under `cgroup`.

No record-kind change, no schema break, no new leaf dependency.

---

## 9. Testing

- **Unit (reader):** fixture `storage_root` with N layers → `layer_count`,
  `layers_bytes` correct; sidecar sizes used without walking; missing sidecar
  falls back to a walk and repopulates; a half-written `manifest.json` →
  `truncated:true`, never a panic.
- **Unit (cgroup):** `io.stat` fixture → `io_rbytes`/`io_wbytes` parsed; deltas
  pairwise.
- **Integration:** a publish records the sidecar size; `collect()` emits a
  `scope:"stack"` sample combining disk unique-layer count/bytes + registry
  refcounts; `--layers` (stack) shows correct refcounts.
- **Sharing (the multi-session case):** two sessions lease overlapping lower
  layers → shared layers show `refcount 2`; `layers_bytes` counts them **once**
  (no double count); each session's `--layers <ws>` shows the shared lowers + its
  **own** upper bytes; releasing one lease drops the refcount (and `pinned_layers`
  if it hits 0), the other lease keeps the layer pinned.
- **Gates:** leaf crate still has no `runtime`/`layerstack`/`daemon` dep
  (boundary test); `cargo build`/`test`/`clippy` clean.
