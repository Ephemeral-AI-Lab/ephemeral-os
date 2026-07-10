# C3 Spec — Three-Way Merge, Full-File Concrete Layers, and Line Provenance

Status: Phase 1 landed; Phase 2 deferred

One spec for the publish-time design selected in
[`experiment_report.md`](./experiment_report.md) (§8): a text-only three-way
auto-merge **plus** line-level ownership/provenance, on top of the **unchanged**
full-file concrete layer format. Compaction (C6) is a separate later doc; this
covers merge, ownership, storage, atomicity, blame, and rollout in one place.

- **Part A — Publish & merge** (§1–§6)
- **Part B — Ownership & provenance** (§7–§12)
- **Part C — Rollout & sizing** (§13–§15)

**Ships in two phases (review decision).** **Phase 1 = blame:** the merge, the
structural origin, and **one** append-after-commit auditability log read by a
`file_blame` operation; the owner travels as an **opaque string**
(`operation:<id>` | `workspace_session:<id>` | `original`). **Phase 2 = chain of
custody:** the `operation` and `workspace_session` logs (§7) plus the typed
`Owner` enum that *resolves* a string to its operation/session detail — they are
**written-but-not-read** by blame, so they are deferred. "(Phase 2)" below marks
deferred surface.

---

## 1. Scope and invariants

**Changes:** `validate_source_paths` (reject-on-mismatch) → `resolve_publish_changes`
(validate, and on mismatch attempt a text three-way merge); each accepted change
carries its route; layerstack returns each resolved line's **structural origin**
and the runtime maps it to an owner string and appends a per-line ownership event
after commit. The owner is **not** an input to layerstack (boundary law).

**Hard invariants (gates from the report — a design that breaks any is wrong):**
- **G1 mount contract.** Every committed layer stays a concrete tree under
  `layers/<id>/`; merge output is a normal `LayerChange::Write`. Provenance is a
  log above layerstack and **never touches the mount path**.
- **G2 universality.** No per-snapshot materialization; works on any base image.
- **G3 atomicity.** No partial changeset; no auditability event referencing a
  non-committed layer (append is after commit); no active-manifest flip before
  staged data is durable.
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
  mod.rs            + pub(crate) use merge, resolve
  plan.rs           CHANGED: carry RouteKind per accepted change (stable order)
  model.rs          CHANGED: + ResolvedChangeset { (LayerChange, origin) }  (no owner ref)
  validate.rs       REMOVED  (folded into resolve.rs)
  resolve.rs        NEW: resolve_publish_changes(...) -> ResolvedChangeset { (LayerChange, origin) }
  merge.rs          NEW: three_way_merge(base, active, command) -> MergeOutcome { bytes, origin };
                    structural Origin {Command | Active(i)} folded in (no origin.rs)
  fingerprint.rs    UNCHANGED  (resolve calls content_fingerprint)
  route.rs          UNCHANGED
  gitignore.rs      UNCHANGED
  opaque_dir.rs     UNCHANGED
src/stack/ops/publish.rs   CHANGED: call resolver; commit BYTES ONLY; return origin (no owner)
src/observability.rs       CHANGED: + merge METRICS (audit counters live in the runtime)
```

**The boundary that keeps this clean (the crux of the design):** layerstack
**merges bytes and computes each final line's structural origin** (this op's
command side vs the active side it inherits) and **returns** it — it stores no
provenance, takes **no owner ref as input**, and knows nothing of operations or
sessions. The runtime above (operation / workspace / daemon) mints `operation_id`,
owns the `storage/` auditability log (§7), maps origin → owner string, and appends
the audit event **after** commit. The opaque owner string is the only shared
vocabulary, and it lives entirely above layerstack.

---

## 4. Data model deltas

**The layerstack publish request is unchanged.** Owner does **not** enter
layerstack (boundary law): it is a single publish-level value the runtime stamps
*after* commit, not a per-change field. So there is **no `AuthoredChange`, no
`OwnerRef`, and no `owned_lines`** in Phase 1 — `changes` stays `Vec<LayerChange>`.

```rust
pub struct PublishValidatedChangesRequest {
    pub base: PublishBase,
    pub changes: Vec<LayerChange>,        // UNCHANGED
    pub protected_drops: Vec<LayerProtectedDrop>,
}

// resolve.rs adds, in the publish RESULT (not the request):
pub struct ResolvedChangeset {
    pub changes: Vec<LayerChange>,                          // the bytes/ops committed
    pub origin: Vec<(LayerPath, Vec<(LineRange, Origin)>)>, // per resolved path (§9)
}
// LineRange { start: usize, len: usize }   // over the committed content's lines
// Origin { Command, Active(usize) }        // structural; no owner ids
```

The resolver derives each line's **structural origin** by diffing committed
content against base: **text → net-changed lines are `Command`, untouched lines
are `Active(i)`; non-text → ineligible (§12), no line ranges.** text-vs-opaque is
*detected* (the same `is_text` check used for merge eligibility), never declared.

The runtime maps `Command` → this publish's owner string and `Active(i)` → the
owner of active line `i` (§9), then appends the event. An `edit` tool's
"owned-lines hint" (skip the diff on huge files) is a **Phase 2** addition to
`resolve.rs`; it is not needed for blame and is omitted now (`None`/infer-by-diff
is always correct).

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

Attribution unit = **line range**, recorded per published `path` — the latest
event is the current blame (§7).

## 6. Identity: one owner string, two kinds

The owner is an **opaque string** in Phase 1: `operation:<id>` |
`workspace_session:<id>` | `original`, plus `unknown` for unprovable lines. The
`file` domain never parses it (§11.1).

**The selection rule (decided):** every CLI operation gets one `operation_id`.
The owner stamped into provenance is **`workspace_session:<session>` if the op
mounted a workspace, else `operation:<operation_id>`.** `exec_command` mounts →
session-owned; `edit`/`write_file` push directly → operation-owned. `original` is
the base-build operation; `unknown` is reserved for genuinely unprovable lines
(legacy with no ancestry, no prior audit event) and is never a tiebreak. There is
no `mixed` (§9).

**Phase 2 — typed `Owner`.** Only when chain-of-custody resolution ships (§8) does
the string get a type:

```rust
enum Owner { Workspace(WorkspaceSessionId), Operation(OperationId) }  // Phase 2
```

Blame needs none of this — it returns the string verbatim — so the enum is
**deferred** with the two stores that back it.

**Minting / boundary invariants (above layerstack):**
- `operation_id` is minted **once at the dispatch boundary** and is an
  **idempotency key** — a retry reuses it (no double-attribution).
- Owner is **not an input to layerstack**: the merge consumes only
  base/active/command bytes, so a wrong/spoofed owner can mis-attribute but
  **cannot corrupt the merge** (boundary law). The runtime stamps owner *after*
  commit; layerstack never re-derives identity and never re-reads a lease by id.

## 7. Stores (SRP, loosely coupled)

Identity is split into independent stores, each with **one responsibility**,
joined **only by opaque ids** — no store imports another's types, no cycles.
**Phase 1 ships exactly one of them**; the other two have no blame-time reader and
are deferred to chain-of-custody (§8):

| Store | Single responsibility | Key | Holds | Phase |
|-------|----------------------|-----|-------|-------|
| **file_auditability_store** | append-only history of per-line ownership | `path` (latest event = current blame) | per publish: path, line_count, default_owner, owner_ranges, content_digest | **1 (read by blame)** |
| operation_store | record each CLI operation | `operation_id` | created_at, operation, args, output, is_error | 2 (chain-of-custody) |
| workspace_session_store | record sessions + ordered operation membership | `workspace_session_id` | created_by_operation_id, `[operation_id by seq]` | 2 (chain-of-custody) |

```rust
// file_auditability_store — append-only; latest event per path = current blame.
// layerstack computes each final line's ORIGIN during the merge and RETURNS it;
// the runtime maps origin→owner string and appends the event AFTER the layer commits.
struct AuditEvent { path, line_count, default_owner: String,
                    owner_ranges: Vec<(LineRange, String)>, content_digest }
fn append(AuditEvent);                  // after the layer commits
fn latest(path) -> Option<AuditEvent>;  // current blame source

// Phase 2 (deferred — no blame-time reader):
// struct OperationRecord { operation_id, created_at, operation, args, output, is_error }
// struct SessionRecord   { workspace_session_id, created_by_operation_id }
// struct SessionMember   { workspace_session_id, operation_id, seq }
```

The owner is an opaque **string** in the event; resolving it to operation/session
detail is the Phase 2 join (§8), one-directional and id-only:

```
file_auditability_store ──owner string──► { workspace_session_store | operation_store }   (Phase 2)
workspace_session_store ──operation_id──► operation_store                                  (Phase 2)
```

**layerstack itself stores no provenance and takes no owner input**: its merge
computes each final line's *origin* (this op's command side, or the active side it
inherits) and returns it; the runtime maps origin → owner string and appends the
event. The owner string is the only shared vocabulary, and it lives entirely above
layerstack.

**No `principal`.** The sandbox is multi-agent, but layerstack + these stores are
a **plugin**: we expose `operation_id`, `workspace_session_id`, and the owner
ref, and **the consumer defines what a "principal"/actor is** and maps our ids to
it in their own project. We do not model it.

## 7.1 Store implementation and path — no DB

The records are **append-only** and tiny (**< 10 operations** in flight), so
**do not use a database.** The store is a trait with a **memory** impl (tests /
ephemeral) and a **durable** impl that is an **append-only NDJSON log loaded into
an in-memory index on open**, matching `sandbox-observability` and leaving
`rusqlite` (declared but unused) unused.

```rust
trait FileAuditabilityStore {
    fn append(&self, event: AuditEvent);                  // after the layer commits
    fn latest(&self, path: &str) -> Option<AuditEvent>;   // current blame source
}
// durable + memory: HashMap<path, AuditEvent> (latest wins), the durable one
// rebuilt from the ndjson segments on open. blame reads `latest` only.
```

`<seq>` is a size-capped rotation segment; on open, scan segments in `<seq>` order
(`O(n)`, `n < 10`) into the index. Appends are `write + fsync` — crash-safe for
append-only data, the **same** guarantee sqlite would give here, with no
schema/migration/bundled-C dependency. (The Phase 2 stores reuse this exact shape.)

**Path (corrected).** The store lives at
`<layer_stack_root>/../storage/file_auditability/file_auditability_<seq>.ndjson`,
derived in `services.rs::from_config` from `config.workspace.layer_stack_root` (the
only root that crate sees). **Observability stays where it is**
(`<daemon-runtime-dir>/observability/`): its root is the daemon socket's parent,
computed in the *daemon* crate, which `from_config` cannot reach — so the "one
`storage/` root for every log" idea is **dropped** (the runtime and daemon crates
cannot share a parent without a new config field, and co-location is cosmetic). If
it is ever wanted, thread a `daemon_runtime_dir` field into `SandboxRuntimeConfig`
from `serve.rs` (`socket_path.parent()`) — not now.

**Decoupled from layer lifecycle** — appended *after* the layer commits:
- a rolled-back publish never appends, so **no event references a non-committed
  layer** — G3's real concern holds;
- C6 squash collapses layer *content*, but the log is **path-keyed and durable**,
  so squash needs **no provenance carry-forward** (a simplification);
- it removes provenance staging/rollback from layerstack's publish path entirely.

**Crash between commit and append (the one tradeoff).** A crash in that window
drops a single event. Reconcile on startup marks the affected path's lines
**`unknown`** — it does **not** "recompute": the committed layer holds the *merged*
bytes, not the pre-merge *command* input (commit is bytes-only, §13), so the
three-way origin cannot be reconstructed. Mark-`unknown` is the only sound option.
Trivial at < 10 ops.

**Why not sqlite:** a linear scan over < 10 append-only rows is instant. Revisit
only at thousands of ops, ad-hoc/cross-sandbox queries, or multiple writers — none
hold for a per-sandbox, single-writer, ephemeral runtime.

## 8. Session creation and membership (Phase 2)

This is the **chain-of-custody** layer — it reads the deferred
`operation`/`workspace_session` stores (§7) and ships with them, not with blame.

- **Creation origin (immutable).** When `exec_command` mints a session without a
  `session_id`, `workspace_session_store` sets `created_by_operation_id` to that
  exec_command's `operation_id` **and records it as the first `SessionMember`**
  (the creator also executed on the workspace). A session can outlive its creator —
  usage is many, origin is one.
- **Membership = the ops that executed (ids only).** Every op that executes on the
  workspace appends a `SessionMember { workspace_session_id, operation_id, seq }`,
  **including the creator.** The store records *which* ops ran, never *what they
  changed*: a workspace publish is a cumulative capture, so we **do not — and
  cannot reliably — attribute a line to a specific op within the session.** Lines
  published via the session are owned by `workspace_session:<id>`; resolving that
  owner yields the creator op and the list of ops that ran, not a per-line op.

Chain of custody — resolved by **composing** the stores, the owner ref as the
only join key:

```
blame(path,line) → owner_ref
  operation:O          → operation_store.get(O)          → operation, args, output, is_error, when
  workspace_session:S  → session_store.created_by(S)=O   → operation_store.get(O)
                       → session_store.operations(S)     → ordered ids of all S did
```

The consumer attaches its own actor/principal concept on top of these ids; the
stores themselves stop at the id.

## 8.1 Publish is session-level (atomic, capture-derived) — Phase 2

A workspace publish is **atomic and derived from the cumulative capture → one
layer, owned by the session — never one layer per operation.** Operations
accumulate in the mounted workspace and are swept *collectively* into the next
publish:

```
session ws-3   (created_by op-12)
  op-12, op-19, op-23  accumulate in the mounted workspace
  ── publish: atomic cumulative capture → ONE layer L8, owner = ws-3 ──
```

Because capture sees the cumulative bytes (not who typed them), per-line "which op
wrote this" is **not provable** from an `exec_command` publish — so the
**session is the owner** of those lines, and `workspace_session_store` records
only membership (`SessionMember` = ids + order). Mapping a layer back to the set
of operations that fed it is a **separate concern, out of scope here.** Queries:
`ops_in_session(S)` (ordered), `session_of(op)` (reverse).

## 9. Attribution (layerstack computes origin; runtime maps to owner)

The merge produces, for each final line of `P`, a **structural origin** — two
variants, no ids, no `Unknown` (ineligible paths never reach the merge, §12).
layerstack returns it; the runtime turns it into an owner string and appends:

```text
layerstack (merge):  each final line → Command | Active(active_line_i)
runtime    (map):    Command   → this publish's owner string
                     Active(i) → owner of active line i, from the latest audit
                                 event for P (which already carries all inherited
                                 history up to active); absent → "original",
                                 or "unknown" if active itself has no event
coalesce equal-owner adjacent lines → AuditEvent.owner_ranges
```

Only **two** structural origins exist — *this op* or *active* — because the active
side's latest event already encodes everything inherited from the base and prior
ops, so a "base-unchanged" line is just `Active(i)`. `mixed` is gone (a command
edit byte-identical to active is `Active`, a no-op); `original`/`unknown` are
*runtime lookup results* of `Active(i)`, not merge origins. The runtime needs only
the latest event for `P` (not the base revision) plus this publish's owner — so
blame stays current-only and squash-safe.

## 10. Auditability event schema

One NDJSON line per published path, appended to
`storage/file_auditability/file_auditability_<seq>.ndjson` **after the layer commits**:

```json
{ "path": "src/main.rs", "line_count": 120,
  "default_owner": "original",
  "owner_ranges": [
    { "start_line": 12, "line_count": 3, "owner": "workspace_session:ws-7" },
    { "start_line": 40, "line_count": 1, "owner": "operation:op-91" } ],
  "content_digest": "sha256:…" }
```

- Keyed by `path`; the **latest event wins** for current blame. `line_count` is
  the file's total lines, so blame tiles `[1..=line_count]` **without any live
  read** (the `file` domain stays decoupled from layerstack, §11.1).
- `default_owner` + sparse ranges keep an event `O(δ)`, not `O(file)`; a wholly
  one-owner file emits just `default_owner` and no ranges (fixes report B5).
- `content_digest` ties the event to the bytes it describes — for
  verification/reconcile only; **blame does not read it**. No `schema_version`
  (ephemeral, never migrates), no `ts` (recency = append order), no `layer_id`
  (the log is path-keyed and outlives any layer).
- Appended **after** the layer commits: a rejected publish appends nothing, and the
  log is **durable across squash** — never deleted with a layer.

## 11. Blame / query — with concrete output

```
blame(path) -> Vec<(Range, owner: String)>      // whole file
```

Resolution: `path → latest AuditEvent(path) → tile [1..=line_count] from
default_owner + owner_ranges`. **Pure store read** — no mount, no layerstack read,
no live `content_digest` check (the post-commit, single-writer append makes the
latest event authoritative; the dropped-append crash case reconciles to `unknown`,
§7.1). **No `owner_of`** (derivable from `blame`) and **no `blame_at`** — blame
answers only the active snapshot (C6 squash discards superseded layers, so
historical blame would be unsound). Lines aren't owned until published.

### Full-file blame output (concrete)

**(a) one session edits a file (`exec_command`, mounted).** Base
`# Project / Setup / Usage`; `ws-7` changed line 2 and appended line 4:

```
$ blame README.md
1  original   # Project
2  ws-7       Installation
3  original   Usage
4  ws-7       License
```

**(b) a mountless `edit` op on top — cross-layer inheritance.** `op-91` changes
line 3; untouched lines keep their owner:

```
$ blame README.md
1  original   # Project
2  ws-7       Installation     ← inherited (untouched by op-91)
3  op-91      Quickstart
4  ws-7       License
```

**(c) three-way merge — two owners.** Base `host / port:8080 / debug:false`;
session `ws-3` changed `port`, op `op-50` (authored vs base) changed `debug`;
disjoint → clean merge:

```
$ blame config.yaml
1  original   host: localhost
2  ws-3       port: 9090       ← active side: inherits the other session's owner
3  op-50      debug: true      ← command side: this op
```

**(d) non-text file — whole-file owner, no line ranges:**

```
$ blame logo.png
(whole file)  op-77   [binary — no line-level attribution]
```

## 11.1 The `file` operation domain (blame)

> **Landed-history note (2026-07-11):** Blame was the only `file` operation in
> this phase. `read`/`write`/`edit` subsequently landed against the same
> `FileService`; the ownership map below reflects the current merged
> contract/catalog/projection/registry architecture.

Blame is exposed as a runtime CLI operation in the `file` domain. The merged
catalog owns its semantic `OperationSpec` and route, the CLI projection owns
its spelling, and the runtime registry owns dispatch.

**Historical service footprint for this phase**
(`crates/sandbox-runtime/operation/src/file/`) — **5 files** (one op did not
warrant the template's `model.rs`/`impls/` split; those operations landed
later):
```
file/
  mod.rs                pub use error::FileError, service::FileService
  error.rs              FileError { NotFound }
  service.rs            mod core, store; pub use core::FileService
  service/core.rs       FileService { store }; blame(&str) -> Result<Vec<BlameRange>, FileError>; BlameRange
  service/store.rs      FileAuditabilityStore (ndjson + in-memory index): append, latest
```

**CLI:** `sandbox-runtime-cli --sandbox-id ID file_blame --path FILE`. The op **name is one token**
(`file_blame`) and the CLI path is `["runtime", "file_blame"]` — the gateway
dispatches by op name and a **family is never a path segment** (e.g.
`exec_command` is in family `command` but its path is
`["runtime","exec_command"]`). A 3-segment `["runtime","file","blame"]`
**cannot route.** Output:
`{ "path": "<file>", "ranges": [ { "start_line", "line_count", "owner" } ] }`.

**Types (minimal — every field earns its place):**
```rust
struct BlameRange { start_line: u64, line_count: u64, owner: String }  // owner opaque
fn blame(&self, path: &str) -> Result<Vec<BlameRange>, FileError>
```
No `FileBlame` wrapper (dispatch adds `path`); no `OwnerRef` newtype — the `file`
domain treats the owner string as opaque, only the runtime resolver interprets
`workspace_session:` / `operation:` / `original` / `unknown`.

**Store** (`FileAuditabilityStore`, the §10 log under `storage/file_auditability/`):
in-memory `HashMap<path, AuditEvent>` loaded from the ndjson segments on open;
`append(event)` after commit; `latest(path)` for blame. `blame(path)` reads the
latest event and tiles `[1..=line_count]` from `default_owner` + sparse
`owner_ranges`, coalescing equal owners. Unknown path → `FileError::NotFound`.
**Blame reads only the store** (no layerstack read, no live file) — `line_count`
in the event is what keeps the `file` domain decoupled. `content_digest` is
verification/reconcile only and is **not** on blame's path.

**Path normalization.** `blame --path` must be normalized through the same
`LayerPath::parse` the audit key uses (`model/mod.rs` strips `./`, `\`, trailing
`/`), or `./src/x` misses an event keyed `src/x` → false `NotFound`.

**Wiring (current ownership — verified against the code):**
- `crates/sandbox-operations/catalog/src/runtime/file.rs` owns `FILE_FAMILY`,
  `FILE_BLAME_SPEC`, and the runtime-owned route; the merged runtime catalog
  aggregates it.
- `crates/sandbox-cli/src/projection/runtime.rs` owns the public command path, usage,
  examples, and `--path` binding.
- `crates/sandbox-runtime/operation/src/operations/registry/file_operations.rs` binds
  `FILE_BLAME_SPEC` to `dispatch_file_blame`; `operations/registry/mod.rs`
  includes its public entry group.
- `services.rs`: `SandboxRuntimeOperations` gains `pub file: Arc<FileService>`;
  `new()` takes a 4th arg. Update the **6 `::new(` call sites** in `tests/`:
  `exec_command.rs:408`, `workspace_session.rs:388` & `:557`,
  `observability_snapshot.rs:125`, `observability_trace.rs:371`,
  `service_graph.rs:92` — each has a tempdir in scope; pass
  `Arc::new(FileService::open(<temp>/storage/file_auditability).expect(..))`
  (`.expect` is not linted; only `unwrap_used` is). `from_config` derives the store
  dir from `config.workspace.layer_stack_root`'s parent (`…/storage/file_auditability`),
  **not** `<daemon-runtime-dir>` (unreachable there, §7.1).
- `lib.rs`: `pub mod file;`.
- **No `serde` derive** — `AuditEvent`/`BlameRange` (de)serialize via `json!` +
  `serde_json::Value`, matching `command_operations` and layerstack `model.rs`.

**Live E2E (`sandbox-runtime-cli --sandbox-id ID file_blame`):** after
`bin/start-sandbox-docker-gateway --rebuild-binary`, create a workspace, produce a
two-owner file (a session writes lines via `exec_command`, then an `edit` op
changes one line), then run the CLI and assert: ranges **tile the file with no
gaps/overlaps**; owners are `workspace_session:<id>` / `operation:<id>` /
`original`; unpublished path → structured not-found; non-text → whole-file owner.
The 2-owner *merge* assertion is **blocked until Part C** (`resolve`/`merge`/append
+ an `edit` op) lands; until then a unit test appends one `AuditEvent` to a tempdir
ndjson and asserts `blame` tiles it (§15 test matrix).

## 12. Edge cases (the honesty rules)

- **Binary / invalid-UTF-8 / NUL / oversized (> cap):** ineligible — a true merge
  falls back to `SourceConflict`; a clean write is owned wholesale by the publishing
  owner, no line ranges. Never claim a false line owner.
- **Ignored paths:** not source-validated → wholesale owner, no precise line
  claims.
- **Legacy file, no prior event:** untouched lines → `original`; if active has no
  audit event either, active-side lines → `unknown`.
- **Identical bytes from two ops:** digest dedupe no-ops the layer; the audit log
  is path-keyed and append-only, so history isn't lost.

---

# Part C — Mechanics, rollout, sizing

## 13. Resolver, merge, and commit path

**Resolver** (`resolve.rs`, replaces `validate_source_paths`, under the writer
lock, whole-changeset, all-resolved-or-one-reject):

```text
for (change, route) in plan.accepted_with_routes():
  if route == Ignored: keep; origin = wholesale Command; continue
  expected = fingerprint(base, path); actual = fingerprint(active, path)
  if actual == expected: keep; origin = diff(base, command) → changed = Command, rest = Active; continue
  match try_auto_merge_source_write(view, request, active, change):
    Clean{bytes, origin} -> push Write{path, bytes}; carry `origin` in the result
    Conflict | Ineligible -> reject SourceConflict{path, expected, actual}
```

The resolver returns `(LayerChange, origin)` per resolved path in the publish
**result** — it takes **no owner input** and never writes provenance.
`try_auto_merge_source_write` reads base/active via `MergedView`, command from the
change (`WriteFile` re-checks spool size), enforces text policy + per-file cap +
per-publish byte budget, then calls `merge.rs`.

**Merge** (`merge.rs`):
```rust
enum Origin { Command, Active(usize) }              // structural — no owner ids, no Unknown
enum MergeOutcome { Clean { bytes: Vec<u8>, origin: Vec<(LineRange, Origin)> }, Conflict, Ineligible }
fn three_way_merge(base, active, command) -> MergeOutcome
```
Myers `O((L+δ)·δ)` line diff base↔active and base↔command; diff3 reconcile; no
`mixed`; builds the structural `origin` in the same pass (no separate `origin.rs`).
Validated by `tests/occ_merge_bench.rs` (B7/B8/B12 + apply-diff round-trip — note
B12 must assert `Active`/inherit for identical-edit, **not** the deleted `mixed`).
Diff internals never escape this module and are never persisted.

**Commit** (`publish.rs`, extends `publish_layer_unlocked`) — bytes only, no
provenance staged:
```text
resolve → if any reject: return (nothing committed)
stage layer dir (BYTES ONLY) → fsync tree + dir → rename → fsync parent → write digest
re-check active == locked active (else remove layer + digest; ManifestConflict)
write new active manifest                         ← layer is now committed
AFTER commit (runtime, above layerstack, SAME critical section as commit): stamp
  this publish's owner string, map origin→owner per resolved path, append one
  AuditEvent each to storage/file_auditability. Serialize commit+append so two
  publishes to one path append in commit order (latest-event-wins stays correct).
  A crash before append → that path reconciles to `unknown` on startup (§7.1; the
  command bytes are gone, so recompute is impossible). No provenance in the layer.
```

## 14. Observability

**Observability ≠ auditability — keep `src/observability.rs` as is.** It holds
operational **metrics** (how the merge ran), not blame. Auditability (who owns
each line) is the separate `file_auditability` log above layerstack; renaming the
metrics module to `auditability.rs` would conflate the two concerns.

- layerstack `src/observability.rs` (merge **metrics**): `automerge_attempted/
  clean/conflict`, `automerge_ineligible{reason}`, `merge_bytes_processed`.
- runtime, where the append happens (**audit** counters): `audit_events_appended`,
  `audit_skipped{reason}`.

No new error variants; keep `PublishRejectReason::SourceConflict` for
failed/ineligible merges.

## 15. Rollout, test matrix, sizing

**Rollout — Phase 1 (blame):** (1) `merge.rs` (Myers+diff3, structural
`Origin{Command,Active}` built in-pass) + round-trip/B7/B8/B12 unit tests, no
wiring ← shared first brick with C6. (2) the `file_auditability` store (ndjson +
index) + the `file` domain + `file_blame` CLI, unit-tested against a hand-written
event (blame is independent of merge). (3) route-per-change in `plan.rs` +
`resolve.rs` returning `(LayerChange, origin)`, wired into
`publish_validated_changes`. (4) runtime stamps the publish owner, maps
origin→owner, appends `AuditEvent` **after commit** in the same critical section;
reconcile-to-`unknown` + counters. (5) default-on only after
conflict/atomicity/caps/audit tests green. **Phase 2 (chain of custody):** the
`operation` + `workspace_session` logs (§7/§8) + typed `Owner`, landed with the
first write-side op (`edit`/`write`).

**Test matrix:** disjoint→clean; overlap→conflict; identical-edit→**inherit-active
`Active`** (no `mixed` — relabel report B12); symlink/delete/type-change→conflict;
binary/invalid-UTF-8→ineligible; oversized→ineligible; `WriteFile` spool re-check;
one-merge+one-conflict→whole reject; mixed source+ignored together; rejected→**no
audit event**; legacy/binary→`unknown`; blame tiles `[1..=line_count]` with no
gaps/overlaps; path normalized (`./src/x` == `src/x`); blame resolves current (no
`blame_at`, pure store read); two publishes to one path append in commit order;
crash-after-commit→reconcile-to-`unknown`; merge uses request base (not active
suffix / not re-read lease).

**Surface estimate (production, excl. tests):**

| File | LoC | Note |
|------|----:|------|
| `merge.rs` | 270–370 | Myers + diff3 + eligibility + in-pass `Origin` (origin.rs folded in) |
| `resolve.rs` | 150–200 | resolver (absorbs `validate.rs`), returns `(change, origin)`, no owner |
| `plan.rs` Δ | ~40 | route per accepted change |
| `model.rs` Δ | ~10 | `ResolvedChangeset` only (no `AuthoredChange`/owner/owned_lines) |
| `publish.rs` Δ | ~30 | commit bytes only; return origin |
| `observability.rs` Δ | ~40 | counters |
| runtime Δ | ~110 | **one** `storage/` log + owner mapping + audit append + reconcile |
| `file/` domain (blame) | ~120 | FileService + store + `blame` + `file_blame` CLI |
| **Phase 1 total** | **~770–920** | + ~600–900 LoC tests; Phase 2 (2 stores + typed `Owner`) adds ~180 later |

**Storage decision (recorded):** full-file concrete layers retained; provenance as
**one append-only auditability log above layerstack** (`storage/file_auditability`,
under `layer_stack_root.parent()/storage/`), appended after commit; **no patch
backend** (fails G2; report §7–8) and **no layer-pinned sidecars** (folded into the
audit log). The `operation` + `workspace_session` identity logs are **Phase 2** (no
blame-time reader). Cold-disk growth is handled later by compaction (C6).
