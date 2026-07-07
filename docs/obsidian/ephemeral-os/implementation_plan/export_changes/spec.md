---
title: Manager Export Changes — sandbox delta to local filesystem
tags:
  - ephemeral-os
  - layerstack
  - manager
  - export
  - implementation-plan
status: implementation_plan
updated: 2026-07-07
---

# Manager Export Changes — sandbox delta to local filesystem

Revised three times on 2026-07-07. First, the CLI-surface simplification:
`--dest` and `--format` only. Second, the data-path correction: the daemon
**unmounts the host workspace bind after building the base**
(`operation/src/services.rs:84`, `detach_workspace_bind_after_base` — it
panics if the unmount fails), so the daemon has no host-visible write path,
ever; delivery streams the delta over the daemon protocol. Third, the
ownership move: export is a **manager operation** (`checkpoint_squash`
precedent), not a runtime one. Crossing the host boundary is operator
authority — the manager is the component that already owns host filesystem
actions (it seeds the base from a host directory at create) and sandbox
records; the runtime CLI drives in-sandbox state only. The manager service
runs on the host, so it owns the host-apply half and both CLIs stay pure
catalog clients — the manager CLI's charter ("a thin protocol client …
never a manager/runtime engine" refers to engines behind the wire; the
apply engine lives in `sandbox-manager`, server-side).

## Goal

Convert the changes a sandbox has accumulated — every published layer above
the base — into a destination on the local (host) filesystem: applied
directly onto a directory (`dir`), or written as a whiteout-preserving
archive (`tar`, `tar-zst`).

The delta is not a patch file: `dir` mode performs real file writes,
deletions, and directory clears on the destination. Applied onto the host
directory the base was seeded from, the result **is** the sandbox's full
merged view — a workable tree — obtained at O(delta) cost because the host
already owns the base bytes.

Policy:

```text
Export is manager-owned. The daemon halves register cli: None (the
squash_layerstack precedent): dispatchable by name, invisible to the
runtime CLI catalog. The runtime surface gains nothing.
Export is read-only on layer-stack storage: no staging, no manifest change,
no sidecars; the spool lives under scratch and dies with the export.
Export exports the published state; live session upperdirs are invisible.
A running session never fails an export: the result names live sessions so
the caller knows unpublished upperdirs may exist, then decides.
The delta is every non-base manifest layer; the base (B*) never leaves.
Applying the delta onto the base's host-origin directory reproduces the
full merged view at delta cost; full materialization is composition, not a
mode.
The daemon never regains a host-visible path: the post-base bind detach is
law. The manager — already the host-authority component — is the only host
writer.
One wire format: the daemon always emits one zstd-compressed,
whiteout-preserving tar; dir and tar are manager-side renderings of that
stream. --format never changes what the daemon does.
```

Speed and space, explicitly (the two optimization targets):

| Cost | Bound | Mechanism |
| --- | --- | --- |
| time, enumerate | O(Σ delta-layer entries) | one newest-first metadata fold over non-base layer dirs |
| time, content read | O(merged delta bytes) | winners only — a path overwritten by a newer layer is never read from the older one |
| time, re-export host writes | O(new bytes) | manager-side skip-unchanged: entries carry source (size, mtime); the applier stamps mtimes on write and skips equal files |
| space, daemon intermediate | O(compressed delta) | one spool file under scratch — no staging tree, no per-layer copies; unlinked when the last chunk is served |
| space, wire | zstd delta × 4/3 | compression before base64 framing; the delta, not the image, crosses the daemon protocol |
| memory | O(unique changed paths) | the winner map holds path → (layer dir, kind), never content |

Re-export re-streams the full compressed delta (the daemon cannot see the
host destination to diff against it) — accepted: squash bounds the delta,
and host writes still converge to O(new bytes) via the skip rule.

## CLI surface

One manager operation under the existing `management` family (no new
family), spec'd in the `sandbox-manager-operations` catalog beside
`checkpoint_squash`:

```text
sandbox-manager-cli export_changes --sandbox-id ID --dest PATH [--format dir|tar|tar-zst]
```

```text
sandbox_id  required, String.  Target sandbox; must be Ready (the existing
                               forward-path gate).
dest        required, Path.   A HOST path, absolute (the manager's CWD is
                               not the caller's — relative paths are
                               rejected). dir format: destination
                               directory, created if missing, applied in
                               place. tar formats: destination archive
                               file; must not be an existing directory.
format      optional, String, default "dir".
                               dir      apply the delta onto dest (writes,
                                        deletions, directory clears, mtime
                                        stamping, skip-unchanged)
                               tar      decompress the stream, write a
                                        plain tar
                               tar-zst  write the stream as received
```

```rust
pub const EXPORT_CHANGES_SPEC: CliOperationSpec = CliOperationSpec {
    name: "export_changes",
    family: "management",
    summary: "Export a sandbox's published changes to a host path.",
    description: "Fold every published layer above the base (newest-wins, \
                  whiteout/opaque aware) into a compressed delta stream, \
                  fetch it from the sandbox daemon, and apply it onto \
                  --dest or write it as an archive. Forwards \
                  export_layerstack and read_export_chunk requests to the \
                  sandbox daemon.",
    args: EXPORT_CHANGES_ARGS,
    cli: Some(CliSpec {
        path: &["manager", "export_changes"],
        usage: "sandbox-manager-cli export_changes --sandbox-id ID --dest PATH [--format dir|tar|tar-zst]",
        examples: &[
            "sandbox-manager-cli export_changes --sandbox-id sbox-1 --dest /home/me/myproject",
            "sandbox-manager-cli export_changes --sandbox-id sbox-1 --dest /tmp/delta.tar.zst --format tar-zst",
        ],
    }),
    related: &["inspect_sandbox", "checkpoint_squash"],
};

const EXPORT_CHANGES_ARGS: &[ArgSpec] = &[
    ArgSpec::required(
        "sandbox_id",
        ArgKind::String,
        "Sandbox id.",
        Some(ArgCliSpec { flag: Some("--sandbox-id"), positional: None }),
    ),
    ArgSpec::required(
        "dest",
        ArgKind::Path,
        "Absolute host destination: directory for dir format, archive file for tar formats.",
        Some(ArgCliSpec { flag: Some("--dest"), positional: None }),
    ),
    ArgSpec::optional(
        "format",
        ArgKind::String,
        "Output format: dir, tar, or tar-zst.",
        Some("dir"),
        Some(ArgCliSpec { flag: Some("--format"), positional: None }),
    ),
];
```

The manager CLI stays a pure catalog client: it builds this one request and
prints the response. `dest` and `format` travel to the **manager service**,
which owns the whole transaction server-side (on the host): forward the
start request, page chunks, decode, render per format, return one merged
result. Nothing about export is special-cased in any CLI.

### Data path

```text
sandbox-manager-cli ── one export_changes request ──▶ manager (host process)
                                                        │ dest guard (absolute, dir/file rules)
                                                        │ forward export_layerstack ──▶ sandbox daemon
                                                        │                                fold → spool → stats
                                                        │ loop: forward read_export_chunk ──▶ daemon
                                                        │       (base64 frames, ≤ 2 MiB raw, eof unlinks spool)
                                                        │ zstd decode → streaming tar apply onto dest
                                                        │ (or archive write, temp + rename)
                                                        ▼
                                            one merged result line back to the CLI
```

Each forward is one bounded request on the existing generic forward path
(`router/forward.rs`: record lookup, Ready gate, `invoke_with_timeout` at
`REQUEST_READ_TIMEOUT_S`) — the chunk loop is many small bounded requests,
never one long-lived stream. The spool-building start request is the one
long call, same profile as `checkpoint_squash`; squash first if the delta
has grown huge.

Scope and trace follow the checkpoint_squash idiom exactly: the manager CLI
op arrives system-scoped with `sandbox_id` in args, and the dispatcher
rebuilds each forwarded runtime request sandbox-scoped
(`CliOperationScope::sandbox(...)`). Every forward — start and all chunks —
reuses the manager request's `request_id`, so the whole export is one trace
across manager and daemon spans.

### Daemon operations (runtime side, both `cli: None`)

Two daemon-local runtime ops back the manager operation, registered like
`squash_layerstack` — dispatch-by-name entries in the layerstack group, no
catalog spec, no runtime CLI visibility, no new entry mechanism.

**`export_layerstack`** (no args): singleflight per layerstack root.
Acquire an `acquire_snapshot` lease → winner fold → emit the tar-zst spool
under `<scratch_root>/.export/<nonce>.tar.zst` → release the lease (layers
are no longer needed once spooled) → return:

```json
{
  "export_id": "exp-7f3a",
  "manifest_version": 12,
  "layers_exported": ["L000002-…", "S000003-…"],
  "entries": { "files": 214, "symlinks": 3, "whiteouts": 2, "opaques": 1 },
  "spool_bytes": 6291456,
  "live_workspace_sessions": ["ws-7"]
}
```

**`read_export_chunk`** `{export_id, offset, limit?}` → one base64 frame:

```json
{ "chunk": "…", "offset": 0, "len": 2097152, "total": 6291456, "eof": false }
```

Default and maximum `limit` 2 MiB raw (≈ 2.8 MiB encoded — comfortably
under the 8 MiB result-envelope precedent). Serving the final byte unlinks
the spool. A new `export_layerstack` replaces any prior spool; leftovers
are reaped with scratch at boot. The manager drives the loop synchronously
inside one `export_changes` invocation, so overlapping readers do not
arise from this design.

## Output contract

The manager returns — and the CLI prints — one compact JSON line on stdout
(exit 0); faults are one `{"error":…}` line on stderr (exit 1). Daemon
stats and host-side apply stats merge into one line. Pretty-printed here.

**dir:**

```json
{
  "manifest_version": 12,
  "format": "dir",
  "layers_exported": ["L000002-…", "S000003-…"],
  "files_written": 214,
  "symlinks_written": 3,
  "deletes_applied": 2,
  "opaque_clears": 1,
  "skipped_unchanged": 190,
  "bytes_written": 18874368,
  "live_workspace_sessions": ["ws-7"]
}
```

**tar / tar-zst** (apply-side fields absent; entry counts come from the
daemon result; `bytes_written` is the archive size on disk):

```json
{
  "manifest_version": 12,
  "format": "tar-zst",
  "layers_exported": ["L000002-…", "S000003-…"],
  "files_written": 214,
  "symlinks_written": 3,
  "whiteouts_emitted": 2,
  "bytes_written": 6291456
}
```

**Empty delta** (base-only manifest — no `no_op` flag, the state speaks for
itself; dir dest untouched, tar dest is a valid empty archive):

```json
{
  "manifest_version": 1,
  "format": "dir",
  "layers_exported": [],
  "files_written": 0,
  "symlinks_written": 0,
  "deletes_applied": 0,
  "opaque_clears": 0,
  "skipped_unchanged": 0,
  "bytes_written": 0
}
```

Counts, not path dumps: the result line stays bounded. Per-path deletion and
skip detail belongs to the observability record (`LAYERSTACK_EXPORT`), the
same division of labor squash uses for byte accounting.

`live_workspace_sessions` (both formats, omitted when empty — the
`faulty_sessions` precedent) lists the sessions alive at snapshot time.
Their uncommitted upperdir changes are invisible to export by design
(invariant 5); the field is a fact, not a fault — capture/publish and
re-export to include them. Session ids come from the existing session
registry; no upperdir walk, no dirty-check machinery.

Faults: daemon-side failures surface as the existing `operation_failed`;
manager-side failures (dest guard, sandbox not Ready, forward errors,
corrupt stream, archive rename) surface through the existing `ManagerError`
→ error-line path — no dedicated error kind. A partially applied dir dest
is recovered by re-running (invariant 4).

## Vocabulary and invariants

| Name | Meaning |
| --- | --- |
| delta manifest | The active manifest's layers with every `B*` layer removed — the ordered (newest-first) set of published change layers. Computed from one snapshot; a predicate over `Manifest`, not a new type. |
| export lease | An ordinary `acquire_snapshot` lease held from fold start to spool completion. Its only job is the existing never-mutate-leased guarantee: squash/GC cannot delete a layer dir mid-read. Zero new lease API. |
| winner fold | Pure newest-first fold over the delta layers' entries producing the winner map. Per path, the newest verdict wins: `File{layer_dir}`, `Symlink{layer_dir}`, `Directory`, `Delete` (whiteout winner), `OpaqueDir` (opaque cut — masks every older layer AND the base under that directory) — the same masking rules `MergedView`/`apply_layer` already encode (`is_kernel_whiteout_meta`, logical `.wh.` prefix, `OPAQUE_MARKER`). Reuses the per-layer walk idiom of `projection/apply.rs`; reads metadata only, never content. |
| winner map | `BTreeMap<LayerPath, Winner>` — O(unique changed paths) memory, deterministic emit order for free. |
| spool | The daemon's one intermediate artifact: winners streamed through `tar::Builder` into one zstd encoder, written to `<scratch_root>/.export/<nonce>.tar.zst`. Entries carry the source file's mode and mtime; `Delete` winners are emitted in the logical OCI encoding (`.wh.<name>`), opaque cuts as `.wh..wh..opq` — never kernel char-dev whiteouts, which need privileges to extract. Unlinked when the last chunk is served, replaced by the next export, reaped with scratch at boot. |
| chunk paging | `read_export_chunk` serves the spool by byte offset in base64 frames (≤ 2 MiB raw). The read_command_lines shape: stable offsets, caller-driven, stateless between calls except the spool file itself. |
| host apply | The manager's dir-mode renderer, streaming the decoded tar: directories ensured (a dest-side symlink or file at a directory position is replaced by a real directory, so writes can never be redirected outside dest); file entries compared (size, mtime) against dest — equal skips, else write then stamp the entry mtime; symlinks recreated; `.wh.<name>` entries `remove_path` the target; `.wh..wh..opq` clears the directory before its siblings apply (existing `apply_layer` semantics — this is what removes base-origin files the sandbox masked with an opaque dir). |
| skip-unchanged | (size, mtime) equality between a tar entry and its dest file. Sound because host apply stamps the entry mtime on every write; stateless because the destination itself carries the watermark. |
| dest guard | Manager-side, before any forward: dest absolute; dir format: create if missing, must be a directory; tar formats: parent must exist, dest must not be a directory; archives written to a nonce-named sibling temp file, renamed into place on success. |

Invariants:

1. **Read-only on layer-stack storage** — export never writes under
   `layer_stack_root`: no staging, no manifest mutation, no sidecars, no
   substitution state. The spool lives under scratch and is transient.
2. **Merged-delta equivalence** — the delta stream applied onto an empty
   destination equals `MergedView` over the delta manifest for every path,
   including directory-only shapes, deletion masking, and opaque cuts. One
   fold, one truth, one unit test pinning them together.
3. **Lease pins sources** — every layer dir the fold or emit reads is pinned
   by the export lease until the spool is complete. A concurrent squash sees
   the lease's newest layer as a boundary, exactly like a session lease; a
   concurrent publish prepends layers the snapshot simply doesn't include.
4. **Idempotent re-run** — re-exporting the same manifest version onto the
   same dest writes zero content bytes on the host (every file entry skips)
   and converges to the identical tree. This is also the crash-recovery
   story for a partially applied dir dest.
5. **Published-only** — no session upperdir, no namespace entry, no live
   mount is ever read. The snapshot manifest is the sole source of truth
   (same axis as the file-operations sessionless backend).
6. **The detach stays; the manager is the only host writer** — export never
   mounts, binds, or re-attaches anything host-visible in the daemon, and
   no runtime-CLI-reachable surface can write to the host. Delivery is
   bytes over the daemon protocol into the manager, full stop.
7. **Archive atomicity** — a tar-format dest is complete-or-absent:
   manager-side temp + one rename. The spool is nonce-named and
   unlink-on-eof, so a crashed export leaves at most one dead file in
   scratch for boot reap.
8. **No durability ceremony** — host apply does not fsync; durability of
   the host directory is the host's concern, and invariant 4 makes re-run
   the cheap answer to any doubt.

## A. Expected file/folder structure with LoC change

`(new ~N)` = new file with estimated LoC; `(+N)` = lines added to existing
file. Calibrated against existing module sizes (`projection/apply.rs` 157,
`projection/mod.rs` 350, service impls 26–110, manager forward impls ~30).

```text
crates/sandbox-runtime/layerstack/
├── src/stack/projection/delta.rs           (new ~140)  delta manifest predicate + winner fold
│                                                       (newest-first, whiteout/opaque masking,
│                                                       metadata-only; shares apply.rs's walk
│                                                       and the whiteout helpers)
├── src/stack/projection/emit_stream.rs     (new ~130)  winner map → tar::Builder → zstd → spool
│                                                       file; logical .wh. re-encoding; mode +
│                                                       mtime on entries; entry counts out
├── src/stack/projection/mod.rs             (+15)       exports; walk visibility shared within
│                                                       projection
└── tests/unit/{export_delta.rs (new ~180), export_stream.rs (new ~160)} · tests/unit.rs (+2)

crates/sandbox-runtime/operation/
├── src/layerstack/service/impls/export.rs  (new ~120)  export_layerstack + read_export_chunk
│                                                       daemon ops, BOTH cli: None (squash
│                                                       precedent — no catalog spec, invisible
│                                                       to the runtime CLI): singleflight per
│                                                       root, lease scope (fold → spool), spool
│                                                       registry {export_id → path, total},
│                                                       chunk reads, unlink-on-eof, live session
│                                                       ids in the start result
├── src/layerstack/service/{model,mod}.rs   (+30)       ExportOutcome, ExportChunk DTOs, exports
├── src/operation.rs                        (+4)        two entries join the layerstack group
└── tests/layerstack_export.rs (new ~180)   daemon-op dispatch: spool + paging to eof, empty
                                            delta, singleflight, spool replacement

crates/sandbox-manager-operations/
└── src/lib.rs                              (+55)       EXPORT_CHANGES_SPEC + args + CLI under
                                                        the existing "management" family; joins
                                                        SPECS (spec-only crate — dispatch stays
                                                        in sandbox-manager); checkpoint_squash's
                                                        related list gains "export_changes"

crates/sandbox-manager/
├── src/operation/management/service/impls/export_changes.rs (new ~80)
│                                                       the manager transaction
│                                                       (checkpoint_squash.rs is the template):
│                                                       parse sandbox_id/dest/format, absolute-
│                                                       dest guard (the InvalidWorkspaceRoot
│                                                       precedent), rebuild the sandbox-scoped
│                                                       runtime request, forward
│                                                       export_layerstack, drive the chunk loop
│                                                       via forward_sandbox_request, hand the
│                                                       stream to the applier, merge the result
├── src/export_apply.rs                     (new ~200)  host-side renderer (crate-root engine
│                                                       module, the daemon_install.rs precedent):
│                                                       zstd decode, streaming tar apply
│                                                       (ensure-dir, skip-unchanged, mtime stamp,
│                                                       .wh./.opq application) or archive write
│                                                       (temp + rename)
├── src/operation/cli_definition/management_operations.rs (+3)
│                                                       import + ManagerOperationEntry::new(
│                                                       &EXPORT_CHANGES_SPEC,
│                                                       dispatch_export_changes) in OPERATIONS
├── src/operation/management/mod.rs         (+1)        re-export dispatch_export_changes
├── Cargo.toml                              (+3)        tar.workspace, zstd.workspace, base64
└── tests/manager_export.rs (new ~240)      catalog + forward loop against a fake daemon;
                                            apply semantics: winners, deletions, opaque
                                            clears, skip, idempotent re-run, archive atomicity

crates/sandbox-observability/
└── src/record.rs                           (+3)        LAYERSTACK_EXPORT

Cargo.toml (workspace)                      (+1)        zstd (tar already present; base64 if not
                                                        already the payload-encoding dep)

sandbox-runtime-cli / sandbox-runtime-operations         (+0)
sandbox-protocol / sandbox-daemon / sandbox-gateway / sandbox-config   (+0)
```

Totals: **5 new source files ≈ 670 LoC**, **≈ +120 LoC** in existing files,
**≈ 760 LoC** of tests → ≈ 1,550 LoC end to end. Zero changes to the
protocol crate, daemon transport, gateway, config, and — after the
ownership move — zero changes to both CLIs and the runtime catalog.

Build order: winner fold (pure) → emit-stream → daemon ops (spool +
chunks) → manager applier (pure over a byte stream, testable without a
daemon) → manager op impl + catalog spec → observability record.

## B. Export workflows

Legend: `Ln` published layer, `B` base, `wh(p)` whiteout of path p,
`opq(d)` opaque marker on directory d. Manifests are newest-first.

### B1. Primary — apply onto the seeding host directory, workable result

```text
host /home/me/myproject seeded the base at create time.
sandbox published: L1: src/a.rs, src/b.rs        L2: src/a.rs (edit), wh(src/b.rs)

sandbox-manager-cli export_changes --sandbox-id sbox-1 --dest /home/me/myproject

daemon:  fold → winners { src/a.rs → File(L2), src/b.rs → Delete, src/ → Dir }
         (L1's a.rs is masked: never read; the base's thousands of files
          never enter the fold)
         spool: src/ · src/a.rs · src/.wh.b.rs
manager: forwards the start request, pages 1 chunk, applies onto
         /home/me/myproject — a.rs overwritten (L2 content, L2 mtime),
         .wh.b.rs deletes b.rs

/home/me/myproject now equals the sandbox's full merged view — base +
delta — and is immediately workable. Total cost: two layer walks, one file
copy, one deletion; the base crossed nothing.
```

Fidelity condition: the result equals the sandbox view exactly when every
path the delta does not touch still carries base-seed content. Host edits
made after seeding survive at untouched paths (export neither knows nor
cares), are overwritten at winner paths, and are removed under
opaque-cleared directories. In-place override is destructive by design —
no backup, no dry-run; the workspace's own VCS is the review-and-undo
surface, and invariant 4 makes re-running always safe. A dest with no base
copy at all gets the sparse delta tree, not a workspace: full copies at
arbitrary locations are composition (seed copy first, then export — see
the `dir-full` deferral for the no-base-copy case).

### B2. Re-export after more publishes — incremental by property

```text
first export @v3 onto /home/me/myproject      214 files applied
publishes land: v5 = [L4 L3 L2 L1 B]          L3, L4 touch 9 paths
second export @v5 onto the same dest:
  wire: full compressed delta streams again (the daemon cannot see dest)
  host: 205 entries equal (size, mtime) → skipped; 9 written
result: files_written 9, skipped_unchanged 205
```

No watermark flag, no server state: the mtime stamped at apply time is the
watermark, carried by the destination itself.

### B3. Masking — opaque directory over base content

```text
base: cfg/dev.yml, cfg/prod.yml     L1: opq(cfg), cfg/prod.yml (rewrite)

fold: cfg → OpaqueDir, cfg/prod.yml → File(L1)
stream: cfg/ · cfg/.wh..wh..opq · cfg/prod.yml
apply onto the seeding dir: the opaque entry CLEARS cfg/ (removing the
base-origin dev.yml the sandbox masked), then prod.yml applies

dest converges to the sandbox view including base files hidden by the
opaque cut — the whole reason the opaque marker rides the stream instead
of being resolved away by the fold.
```

Honesty boundary: a path that *leaves* the delta between exports without a
masking winner (today only `amend_path` rewriting head can do this) is not
re-converged on a stale dest — the delta no longer describes it. The
contract is "dest reflects this delta", not "dest is synchronized with
delta history"; a fresh dest or re-seeded copy is the escape hatch. Stated
here so nobody retrofits rsync semantics later.

### B4. Archive — lossless, transportable delta

```text
same v3 as B1, --format tar-zst --dest /tmp/delta.tar.zst

manager writes the stream as received: .delta.tar.zst.<nonce> → rename
entries: src/ · src/a.rs · src/.wh.b.rs        (logical OCI encoding)
result: files_written 1, whiteouts_emitted 1, bytes_written = archive size
```

The archive is a valid OCI-style layer: deletions survive, it applies later
onto any base copy, and it compressed before crossing the wire.

### B5. Concurrent squash — the lease does all the work

```text
export starts @v13, lease snapshot [L12 L11 L10 … B]
checkpoint_squash runs mid-spool:
  export lease's newest layer is a boundary → squash blocks straddling it;
  replaced layers the export still reads stay on disk (never mutate or
  delete a leased layer dir — existing law, no new code)
spool completes on its snapshot → lease releases → refcount GC reclaims
whatever only the export pinned; chunks keep serving from the spool, which
depends on no layer
next squash compacts what this one had to skip
```

No retry, no invalidation, no special case: invariant 3 plus the spool's
independence from layer dirs is the whole story. The two manager
operations compose: squash to compact, export to deliver.

## C. Non-goals and deferrals

- **Live-session export** — upperdirs are captured/published by the existing
  session lifecycle; export reads published truth only and reports live
  sessions in the result line rather than failing on them. Publish first.
- **Full materialization (`dir-full`)** — a self-contained snapshot
  including the base, for a dest with no host copy of the seed or one that
  has diverged. Composition covers the common cases: in-place override
  (B1), and a full copy at an arbitrary location = copy the seed there
  (`cp -a` / fresh clone), then export onto the copy. When a no-base-copy
  full export is genuinely needed it is one new `format` value streaming
  ALL layers — the base is immutably available daemon-side
  (`base/B000001-base`), and `MergedView::project` already encodes the
  semantics — at documented O(image) wire and time cost. Deliberately not
  v1.
- **Dest defaulting from the sandbox record** — the manager knows the host
  workspace the base was seeded from (`inspect_sandbox` already surfaces
  the workspace root), so `--dest` could default to it. Deferred: a
  zero-extra-args invocation whose default behavior deletes host files is
  the wrong ergonomic to ship first; revisit once usage exists.
- **Server-side dest diffing** — wire bytes are always the full compressed
  delta. A future manifest-version watermark could skip spooling entirely
  when nothing changed; deferred until re-export frequency proves it
  matters.
- **Bounded parallel apply** — host apply is a serial tar stream; a width-N
  pool would need out-of-order extraction for marginal gain on a
  local-filesystem write path. Not planned.
- **Byte-level deltas between exports** — skip-unchanged captures the win at
  a fraction of the complexity.
- **Path filtering, dry-run, checksum flags** — deliberately absent. Per-
  layer digests in `.layer-metadata/` remain the integrity story.

## Decision log

1. **Two user arguments beyond the sandbox selector** (user review,
   2026-07-07): `--dest`, `--format`. Incrementality, deletion policy,
   parallelism, and scope became defaults/properties instead of flags.
2. **Deletions apply by default in dir mode**: "convert the changes"
   includes deletions; a fresh dest makes them no-ops; counts in the result
   line, per-path detail in the observability record (bounded result,
   squash precedent).
3. **No new family, no new entry mechanism**: the manager op joins the
   existing `management` family; the daemon ops register with `cli: None`
   (squash_layerstack precedent) so the runtime surface gains nothing.
4. **Reuse over invention**: winner fold lives beside `projection/apply.rs`
   and shares its walk and whiteout vocabulary; `MergedView` stays the
   read-path truth; equivalence is invariant 2 and a unit test, not a
   shared abstraction forced before it's needed.
5. **mtime-stamped writes** power skip-unchanged (tar entries carry source
   mtime; the applier stamps on write) — chosen over a server-side
   watermark to keep the daemon stateless about destinations it cannot see.
6. **`zstd` enters `[workspace.dependencies]`**; `tar` is already there.
   Compression is worth a dependency because the delta crosses the wire
   base64-framed — fewer raw bytes is the dominant win.
7. **Logical `.wh.` encoding on the stream** (kernel char-dev whiteouts
   need privileged extraction); host apply consumes whiteouts and opaque
   markers directly and never writes them to a dir dest.
8. **No fsync in dir mode**: idempotent re-run is the recovery story;
   tar dests get temp+rename because a partial archive is corrupt, not
   merely incomplete.
9. **Delta, not full materialization** (design review, 2026-07-07): the
   base seed is host-origin, so a full export re-copies bytes the host
   already has at O(image); the delta yields the full view by composition
   onto the host's base copy at O(delta) and is the only form that carries
   deletions explicitly. `dir-full` stays a documented deferral.
10. **Running sessions report, never fail** (design review, 2026-07-07):
    session existence is not evidence of missing changes, sessions never
    mutate published layers (no consistency hazard to guard), and blocking
    export under long-lived sessions would make it unusable. Silent
    omission is equally wrong, so the result line carries
    `live_workspace_sessions` — the squash report-don't-fail precedent.
11. **Chunk streaming, not bind-mount writes** (data-path review,
    2026-07-07): the daemon unmounts the host workspace bind after base
    build (`services.rs:84`; panic on failure), so "write through the
    bind" was never available. One tar-zst spool, paged chunks
    (`read_command_lines` precedent). Costs accepted: base64 4/3 framing
    over zstd, O(compressed delta) spool in scratch, full-delta wire on
    re-export.
12. **Opaque markers ride the stream**: the winner fold must NOT resolve
    opaque cuts away, because they are the only record that base-origin
    files under that directory were masked — host apply's clear-directory
    is what makes dest converge to the sandbox view (B3).
13. **Manager-owned, manager-applied** (user direction, 2026-07-07):
    crossing the host boundary is operator authority, and the manager is
    the component that already touches the host filesystem (base seeding)
    and holds the sandbox records. The manager service — a host process —
    drives the forward loop and owns the applier, so BOTH CLIs stay pure
    catalog clients (the previous revision had special-cased the runtime
    CLI). `--sandbox-id` joins the surface as the ordinary manager-op
    selector; each forward is one bounded request on the existing
    `router/forward.rs` path; dest must be absolute because the manager's
    CWD is not the caller's.
