# Layerstack Observability — Side Spec

Status: ready-to-implement (additive to the main spec).

A companion to `README.md`. The main spec sources resource metrics from two pure
`&Path → struct` readers moved into the leaf crate (`collect/{cgroup,disk}`, §6),
and current entity state from the runtime's live `observability_snapshot()` (§4.2).
This side spec adds **layerstack** to both sides of that line:

- a third pure reader — `collect/layerstack.rs` — for the **structural** facts
  that live on disk (`manifest.json` + the `layers/` tree);
- a **leased / booked-by** breakdown drawn from the runtime, because that state
  lives only in process memory and is not on disk.

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
| **leased heads + per-layer refcount** | **process memory** | ❌ never persisted | `lease/registry.rs:18` (`refcounts: BTreeMap<LayerRef,usize>`), `lease_head_layers`, `stack/mod.rs:116` (`leased_layers`), `:121` (`active_count`) |
| protected bytes | computed by reclaim | ❌ runtime call | `reclaim_unpinned_layers/mod.rs:56` (`protected_pinned_bytes`) |

**Sharing model (load-bearing — get this right).** There is **one** `LayerStack`
per sandbox (`storage_root`), shared by every workspace session; the lease
registry is keyed by the stack root (`shared_registry_for_root`, `registry.rs`).
Each session takes its **own** lease over a manifest (its lower layers) and has
its **own** upperdir / workdir / mount namespace — but **lower layers are
shared**: the same layer can be leased by many sessions at once. The registry
tracks this with a **per-layer refcount** (`refcounts: BTreeMap<LayerRef,usize>`,
`registry.rs:18`; `increment_layers`/`decrement_layers` on acquire/release) and the
set of **leased heads** (`lease_head_layers`). A workspace leases a **head** layer
and thereby mounts every layer below it, so a layer is **needed** while any lease
sits at or above it, and **squashable** when none does. Owners are not stored
(`owner_request_id` is discarded, main spec §5). So:

- **Needed-ness is a stack-global fact** (any lease, any workspace), not
  per-workspace.
- The two distinct per-layer relationships are **leased** (a workspace's head *is*
  this layer) and **booked by** (higher leased heads that need it as a base).
- **Bytes of lower layers belong to the stack, counted once.** Summing them
  per-session double-counts every shared layer.
- **Only upperdir/workdir/namespace are session-local.**

This dictates the scoping: `scope:"stack"` carries the shared inventory (per-layer
bytes once + leased/booked-by); `scope:"<ws>"` carries only the session's **private**
bytes (its upperdir — the existing disk sample — and workdir) plus *which* layers
its lease references (ids, not bytes).

**Rule of thumb:** structure → disk reader (leaf crate). Pins → runtime
(`observability_snapshot()`), exactly like in-flight executions in the main spec.
Do **not** reconstruct pins by folding `lease.acquired` − `lease.released` from
the log: it breaks on daemon restart (the in-memory registry resets to empty
while the log still shows old acquires) and on crash (a `lease.acquired` with no
release → false "needed forever"). The in-memory registry is the only truth.

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
{"ts":1719500010000,"kind":"sample","sandbox":"eos-abc","component":"sandbox-daemon","scope":"stack","layer_count":5,"layers_bytes":2670000,"needed_layers":4,"squashable_layers":1,"squashable_bytes":40960,"active_leases":2,"revision":"r6","truncated":false}
```

`layers_bytes` counts each unique layer **once** (never per-session);
`needed_layers` = layers some lease still needs (directly leased, or booked by a
higher lease); `squashable_layers` / `squashable_bytes` = what no lease needs, so
squash can drop it; `active_leases` = live sessions holding a lease. Bounded and
flat (no per-layer array — that would blow the line cap, main spec §6). It answers
"is the stack growing / is squashable dead-weight piling up" via the Case D delta
machinery. **Per-session private bytes stay the existing `scope:"<ws>"` disk
sample** (upperdir; add workdir if wanted) — that's where the per-workspace
numbers live, with no double-counting of shared layers.

**Rendered — `observability layerstack --window-ms …` (cli-observability.md §4.5):**

```console
$ sandbox-cli observability layerstack --sandbox-id eos-abc --window-ms 60000
scope stack   window 60s   (Δ computed at read)

  t(+s)   layers   Δlayers   unique_bytes   Δbytes     squashable   leases
  00.0      5         –         2.55MB         –           1          2
  60.0      6        +1         2.88MB      +330KB         2          2
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
stack r6   5 layers (4 needed, 1 squashable)   2.55MB   2 leases

  layer        bytes    leased   booked by    status
  l0 (base)    1.80MB     0       l2, l3
  l1           480KB      0       l2, l3
  l2            80KB      1       l3
  l3           156KB      1       —
  l4            40KB      0       —            squashable
```

`leased` = how many workspaces book this layer as their head (direct); `booked by`
= the higher leased layers that need it mounted as a base. A layer is `squashable`
when **no** lease sits at or above it (`leased 0` and `booked by —`); everything
else is `needed`. Bytes are the unique per-layer size (cache §6). There is **no
owner column** — owners aren't stored.

**Per session** (`view:"layerstack"`, `workspace:"ws-7"`): the layers this session
mounts (its head + every layer below it), flagged by which other workspaces share
them, plus its **private** upper/workdir:

```console
$ sandbox-cli observability layerstack --sandbox-id eos-abc --workspace ws-7
workspace ws-7   head l3   mounts l0..l3 (4 layers)   upper 156KB   workdir 8KB

  layer        bytes    shared with
  l0 (base)    1.80MB   ws-9
  l1           480KB    ws-9
  l2            80KB    ws-9
  l3           156KB    — (only ws-7)
  upper        156KB    private
```

`shared with` names the other workspaces also mounting the layer; `l3` (ws-7's
head) is unique to it. Only `upper`/`workdir` are this session's own bytes (from
the `disk.rs` walk) — the lower layers' bytes belong to the stack.

### 4.3 cgroup `io.stat` (bonus, main spec `cgroup.rs`)

Extend the cgroup sample with two fields read from `/sys/fs/cgroup/<scope>/io.stat`:

```json
{"ts":...,"kind":"sample","sandbox":"eos-abc","component":"sandbox-daemon","scope":"ws-7","cpu_usec":4100000,"mem_cur":21000000,"mem_max":268435456,"io_rbytes":1048576,"io_wbytes":4194304,"disk_bytes":1320000,"files":340}
```

Deltas computed at read like every other counter (main spec §4.4).

### 4.4 Snapshot view addition (main spec §4.2)

Needed/squashable counts are **stack-global**, so they get their own line in the
`observability` snapshot (main spec §7.1); the per-workspace rows show only what's
session-local (its head, how many layers it mounts, private upper bytes). Both come
from the live registry + disk reader:

```console
$ sandbox-cli observability snapshot --sandbox-id eos-abc
sandbox eos-abc   state ready

  stack   r6   5 layers (4 needed, 1 squashable)   2.55MB   2 leases

  workspaces
    ws-7   active   profile=default   head l3   mounts 4   upper 156KB
    ws-9   active   profile=default   head l2   mounts 3   upper  88KB
```

Note `ws-7` (head l3) and `ws-9` (head l2) share `l0`–`l2`; the 2.55MB is counted
once at the stack line, not added per workspace.

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
  measured **once**, not N times — the cache and the lease model line up.
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
stays a leaf (main spec §6 boundary). The leased / booked-by merge happens in the daemon,
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
   needed/squashable counts to the snapshot view rows; surface `io.stat` under `cgroup`.

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
  needed/squashable counts; `--layers` (stack) shows correct `leased` / `booked by`.
- **Sharing (the multi-session case):** two sessions with heads at different layers
  → a lower layer shows the higher heads under `booked by`; `layers_bytes` counts
  each layer **once** (no double count); each session's `--layers <ws>` shows which
  lowers are `shared with` others + its **own** upper bytes; releasing the higher
  lease flips the now-unneeded layers to `squashable`, while layers a remaining
  lease still sits at or above stay `needed`.
- **Gates:** leaf crate still has no `runtime`/`layerstack`/`daemon` dep
  (boundary test); `cargo build`/`test`/`clippy` clean.
