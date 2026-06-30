# C3 Spec — Three-Way Merge, Full-File Concrete Layers, and Line Provenance

Status: draft for review

One spec for the publish-time design selected in
[`experiment_report.md`](./experiment_report.md) (§8): a text-only three-way
auto-merge **plus** line-level ownership/provenance, on top of the **unchanged**
full-file concrete layer format. Compaction (C6) is a separate later doc; this
covers merge, ownership, storage, atomicity, blame, and rollout in one place.

- **Part A — Publish & merge** (§1–§6)
- **Part B — Ownership & provenance** (§7–§12)
- **Part C — Rollout & sizing** (§13–§15)

---

## 1. Scope and invariants

**Changes:** `validate_source_paths` (reject-on-mismatch) → `resolve_publish_changes`
(validate, and on mismatch attempt a text three-way merge); each accepted change
carries its route; publish writes a provenance sidecar per resolved text write;
the publish request carries an **owner ref**.

**Hard invariants (gates from the report — a design that breaks any is wrong):**
- **G1 mount contract.** Every committed layer stays a concrete tree under
  `layers/<id>/`; merge output is a normal `LayerChange::Write`. Sidecars live
  under `.layer-metadata/` and are **never mounted**.
- **G2 universality.** No per-snapshot materialization; works on any base image.
- **G3 atomicity.** No partial changeset; no visible sidecar without its layer; no
  active-manifest flip before all staged data is durable.
- **G4 byte-exact.** Merge on line slices (preserve CRLF / missing final newline);
  non-text/oversized inputs are ineligible and fall back to today's
  `SourceConflict`.

---

## 2. Code anchors (today)

`src/stack/ops/publish.rs::publish_validated_changes` → `publish_layer_unlocked`;
`src/stack/publish/plan.rs::plan_publish`;
`src/stack/publish/validate.rs::validate_source_paths` (replaced);
`src/stack/publish/fingerprint.rs::content_fingerprint`;
`src/stack/publish/route.rs::RouteKind {Source, Ignored}`;
`src/stack/projection/mod.rs::MergedView::read_entry → MergedEntry`;
`src/model/mod.rs::LayerChange {Write, WriteFile, Delete, Symlink, OpaqueDir}`;
metadata dir `.layer-metadata/` (`LAYER_METADATA_DIR`).

---

## 3. Module boundary and file layout

```
src/stack/publish/
  mod.rs            + pub(crate) use merge, provenance, resolve
  plan.rs           CHANGED: carry RouteKind per accepted change (stable order)
  model.rs          CHANGED: + owner ref + Authorship on request; + ResolvedChangeset
  validate.rs       REMOVED  (folded into resolve.rs)
  resolve.rs        NEW: resolve_publish_changes(...) -> ResolvedChangeset
  merge.rs          NEW: three_way_merge(base, active, command) -> MergeOutcome
  provenance.rs     NEW: Owner, ProvRange, sidecar read/write, attribution
src/stack/ops/publish.rs   CHANGED: call resolver; stage/rollback sidecars
src/observability.rs       CHANGED: + merge/provenance counters
```

**The boundary that keeps this clean (the crux of the design):** layerstack
stores an **opaque owner string** and merges bytes. It does **not** know what an
operation or a session *is*. Minting `operation_id`, the records (§8), and the
"mounted → session / mountless → operation" decision (§7) all live **above**
layerstack (runtime/operation or daemon), which resolves the owner and passes it
in the request. Layerstack owns only `.layer-metadata/provenance/`.

---

## 4. Data model deltas

```rust
pub struct PublishValidatedChangesRequest {
    pub base: PublishBase,
    pub changes: Vec<AuthoredChange>,     // was Vec<LayerChange>
    pub protected_drops: Vec<LayerProtectedDrop>,
}

pub struct AuthoredChange {
    pub change: LayerChange,              // the bytes/op to commit (enum unchanged)
    pub owner: OwnerRef,                  // opaque "workspace_session:.." | "operation:.."
    pub authorship: Authorship,
}

pub enum Authorship {
    Hunks(Vec<Hunk>),  // edit tool: exact ranges vs base → ownership by construction
    WholeFile,         // write_file / overlay capture: infer changed ranges by diff
    Opaque,            // non-text / wholesale: no line ownership
}
// Hunk { old: Range<usize>, new_lines: Vec<Vec<u8>> }

pub type OwnerRef = String;               // validated charset; opaque to layerstack
```

`Hunks` lets the resolver skip the base↔command diff entirely (the editor already
knows what it changed). `WholeFile`/`Opaque` reproduce today's behavior with
attribution attached.

---

# Part B — Ownership & provenance

## 5. Definition of ownership

> **A line's owner is the change that last made a *net* modification to that exact
> line content in the visible history.** (git-blame semantics at publish
> granularity.)

Two rules make it total and unambiguous:
- **Transfer on net change only.** If a write produces bytes identical to what is
  already there, it is not a modification → no ownership transfer.
- **Inherit on no change.** Untouched lines keep the owner the layer below
  recorded.

Attribution unit = **line range**. Stored per `(layer_id, path)`, guarded by
`content_digest`.

## 6. Identity: one owner ref, two kinds

```rust
enum Owner {
    Workspace(WorkspaceSessionId),   // op mounted a workspace; the workspace decides publish
    Operation(OperationId),          // mountless edit/write: direct push to changeset
}
// stored origin = Owner | Unknown        (no `mixed`; `original` is Owner::Operation(op:base))
```

**The selection rule (decided):** every CLI operation gets one `operation_id`.
The owner stamped into provenance is **`Workspace(session)` if the op mounted a
workspace, else `Operation(operation_id)`.** `exec_command` mounts → session-owned;
`edit`/`write_file` push directly → operation-owned.

`Unknown` is reserved for genuinely unprovable lines (legacy with no ancestry,
missing parent sidecar). It is never a tiebreak. We dropped `mixed` (§9) and
folded `original` into the base-build operation `op:base`, so there is exactly
**one owner concept plus `Unknown`**.

**Minting invariants (above layerstack):**
- `operation_id` is minted **once at the dispatch boundary** and is an
  **idempotency key** — a retry reuses it (no double-attribution).
- Layerstack uses the owner ref **only** for metadata; a wrong/spoofed ref can
  mis-attribute but **cannot corrupt the merge** (correctness comes from
  base/active/command bytes — boundary law).
- The owner + base anchor travel **in the publish request**; layerstack never
  re-derives identity and never re-reads a lease by id.

## 7. Records (kept minimal, live above layerstack)

```rust
struct OperationRecord {          // one per CLI op, always
    operation_id, principal, kind /*Edit|Write|Exec*/, base_revision, created_at, outcome,
}
struct WorkspaceSessionRecord {   // one per session, written at creation
    workspace_session_id, created_by_operation_id, principal, base_revision, created_at,
}
```

These two tiny rows are the entire identity store. Sidecars hold only the owner
ref; the records carry who/what/when, joined by id.

## 8. Tracing a lazily-created workspace session

When `exec_command` runs without a `session_id`, it mints the session. **The
session's creation_origin is the operation that created it** — recorded once,
immutable:

```
line ─owned-by→ workspace_session:S
                    │  WorkspaceSessionRecord.created_by_operation_id
                    ▼
               operation:O_create ─→ principal, base_revision, created_at
```

- `created_by_operation_id` is set at creation and never changes; later ops *use*
  the session.
- One row per session — never duplicated into sidecars.
- A session can outlive its creator op; usage is many, origin is one.

Full chain of custody, always recoverable:
```
blame(path,line) → Owner
  Operation(O) → OperationRecord(O)        → principal, base, when      (mountless)
  Workspace(S) → WorkspaceSessionRecord(S) → created_by_operation_id O  → OperationRecord(O)
```

## 9. Attribution algorithm (the resolver's provenance pass)

At publish of file `P` into new layer `L_new`, inputs: authored ranges (from
`Hunks`, or diff-inferred for `WholeFile`), the **base sidecar** (`P` in the
request base), and — only if active diverged — the **active sidecar**. Per final
line of merged `P`:

```text
base-unchanged          → inherit base_sidecar.owner(line)
command side (this op)   → this change's owner            (exact for Hunks)
active side (other op)   → inherit active_sidecar.owner(line)
command == active bytes  → inherit active owner   ← replaces `mixed`: identical = no net change
ineligible (binary/etc.) → Unknown
coalesce equal-owner adjacent lines → ranges
```

Dropping `mixed` is what removes a whole merge branch *and* a stored origin kind:
an identical edit against active is a no-op, so ownership simply stays with
active. Forensics (two ops independently touched the file) remain visible in the
operation log.

## 10. Sidecar schema and storage

```json
{
  "schema_version": 1,
  "layer_id": "L42",
  "path": "src/main.rs",
  "content_digest": "sha256:…",
  "default_owner": "original",
  "ranges": [
    { "start_line": 12, "line_count": 3, "owner": "workspace_session:ws-7" },
    { "start_line": 40, "line_count": 1, "owner": "operation:op-91" }
  ]
}
```

- Location `.layer-metadata/provenance/<layer_id>/<path>.json` — **not mounted**.
- Key `layer_id + path`; `content_digest` rejects stale metadata.
- `default_owner` + sparse ranges: a 99%-original file lists only its few
  owned ranges (fixes the report's B5 sidecar-overhead finding).
- **Skip the sidecar entirely when a file is wholly one owner** — attribute it
  from a per-layer default. Keeps provenance `O(δ)`, not `O(file)`.
- Staged, renamed, and rolled back **atomically with the layer** (§13). Rejected
  publish writes **no** sidecar.

## 11. Blame / query — and does it work against the file?

```
provenance_of(path, line) -> Owner       // current committed version
blame(path)               -> Vec<(Range, Owner)>
blame_at(path, manifest)  -> …            // a specific historical manifest/layer
```

Resolution: `path → active manifest → head layer with concrete P → sidecar(layer_id,P)`
→ binary-search ranges. **It blames the committed manifest version of the file,
not an unpublished working copy** — lines aren't owned until published. It is a
pure metadata read: **no mount required** (consistent with the mountless
edit/write path), and the `content_digest` check guarantees the sidecar matches
the bytes it describes. For historical blame, query a specific layer/manifest.

## 12. Edge cases (the honesty rules)

- **Binary / invalid-UTF-8 / NUL / oversized (> cap):** `Opaque` → whole file
  owned wholesale by the publishing owner, line ranges omitted/`Unknown`. Never
  claim a false line owner.
- **Ignored paths:** not source-validated → wholesale owner, no precise line
  claims.
- **Legacy file, no base sidecar:** untouched lines → `op:base` (`original`); if
  active also lacks a sidecar, active-side lines → `Unknown`.
- **Identical bytes from two ops:** digest dedupe no-ops the layer; sidecar keyed
  by `layer_id+path` so history isn't lost.

---

# Part C — Mechanics, rollout, sizing

## 13. Resolver, merge, and commit path

**Resolver** (`resolve.rs`, replaces `validate_source_paths`, under the writer
lock, whole-changeset, all-resolved-or-one-reject):

```text
for (change, route) in plan.accepted_with_routes():
  if route == Ignored: keep; provenance = wholesale owner (or skip); continue
  expected = fingerprint(base, path); actual = fingerprint(active, path)
  if actual == expected: keep; provenance = attribute(change, base_sidecar, owner); continue
  match try_auto_merge_source_write(view, request, active, change):
    Clean{bytes, ranges} -> push Write{path, bytes}; provenance = ranges
    Conflict | Ineligible -> reject SourceConflict{path, expected, actual}
```

`try_auto_merge_source_write` reads base/active via `MergedView`, command from the
change (`WriteFile` re-checks spool size), enforces text policy + per-file cap +
per-publish byte budget, then calls `merge.rs`.

**Merge** (`merge.rs`):
```rust
enum MergeOutcome { Clean { bytes: Vec<u8>, ranges: Vec<ProvRange> }, Conflict, Ineligible }
fn three_way_merge(base, active, command, owner_active, owner_command) -> MergeOutcome
```
Myers `O((L+δ)·δ)` line diff base↔active and base↔command; diff3 reconcile; no
`mixed`; builds provenance ranges in the same pass. Validated by
`tests/occ_merge_bench.rs` (B7/B8/B12 + apply-diff round-trip). Diff internals
never escape this module and are never persisted.

**Commit** (`publish.rs`, extends `publish_layer_unlocked`):
```text
resolve → if any reject: return (nothing staged)
stage layer dir + stage sidecars under staging .layer-metadata/provenance/<layer_id>/...
fsync tree + dir → rename layer into place → fsync parent → write digest
re-check active == locked active (else remove layer+digest+sidecars; ManifestConflict)
write new active manifest
on any post-stage failure: remove layer dir, digest, AND sidecar dir together
```

## 14. Observability

Counters: `automerge_attempted/clean/conflict`, `automerge_ineligible{reason}`,
`merge_bytes_processed`, `provenance_sidecars_written`,
`provenance_skipped{reason}`. No new error variants; keep
`PublishRejectReason::SourceConflict` for failed/ineligible merges.

## 15. Rollout, test matrix, sizing

**Rollout:** (1) `merge.rs` + round-trip/B7/B8/B12 unit tests, no wiring ← shared
first brick with C6. (2) `provenance.rs` + sidecar tests. (3) route-per-change in
`plan.rs` + `resolve.rs` wired into `publish_validated_changes`. (4)
sidecar stage/rollback + counters. (5) thread `owner`/`authorship` from runtime.
(6) default-on only after conflict/atomicity/caps/provenance tests green.

**Test matrix:** disjoint→clean; overlap→conflict; identical-edit→inherit-active
(no `mixed`); symlink/delete/type-change→conflict; binary/invalid-UTF-8→
ineligible; oversized→ineligible; `WriteFile` spool re-check; one-merge+one-conflict
→ whole reject; mixed source+ignored together; `Hunks` exact attribution;
`WholeFile` diff attribution; rejected→no sidecar; legacy/binary→`Unknown`; digest
guards stale sidecar; blame resolves current + historical; session→creator-op
chain; merge uses request base (not active suffix / not re-read lease).

**Surface estimate (production, excl. tests):**

| File | LoC | Note |
|------|----:|------|
| `merge.rs` | 250–350 | Myers + diff3 + eligibility |
| `provenance.rs` | 200–300 | Owner, ranges, sidecar read/write, digest guard |
| `resolve.rs` | 150–200 | resolver (absorbs `validate.rs`) |
| `plan.rs` Δ | ~40 | route per accepted change |
| `model.rs` Δ | ~80 | `AuthoredChange`, `Authorship`, owner |
| `publish.rs` Δ | ~60 | sidecar stage/rollback |
| `observability.rs` Δ | ~40 | counters |
| runtime/operation Δ | ~120 | mint owner + records, pass into request (above layerstack) |
| **Production total** | **~900–1,200** | + ~700–1,000 LoC tests |

**Storage decision (recorded):** full-file concrete layers retained; provenance
as metadata sidecars; identity as two minimal records above layerstack; **no
patch backend** (fails G2; report §7–8). Cold-disk growth is handled later by
compaction (C6), not by changing this format.
