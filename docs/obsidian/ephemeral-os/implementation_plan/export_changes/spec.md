---
title: Runtime Export Changes — sandbox delta to local filesystem
tags:
  - ephemeral-os
  - layerstack
  - runtime
  - export
  - implementation-plan
status: implementation_plan
updated: 2026-07-07
---

# Runtime Export Changes — sandbox delta to local filesystem

Revised after the CLI-surface simplification review: the operation takes
exactly two arguments, `--dest` and `--format`. Everything the dropped flags
did becomes a default or a property: incrementality is the skip-unchanged
rule (no watermark flag), whiteout deletions apply unconditionally in `dir`
mode (the destination reflects the delta, period), live sessions are out of
scope (publish/capture first), parallel copy is a deferred measured
experiment, not an option.

## Goal

Convert the changes a sandbox has accumulated — every published layer above
the base — into a destination on the local filesystem: either materialized
onto a directory (`dir`) or as a whiteout-preserving archive (`tar`,
`tar-zst`). The base layer is never exported; it is the host's own seed
content.

Policy:

```text
Export is read-only on storage: no staging, no manifest change, no sidecars.
Export exports the published state; live session upperdirs are invisible.
A running session never fails an export: the result names live sessions so
the caller knows unpublished upperdirs may exist, then decides.
The delta is every non-base manifest layer; the base (B*) never leaves.
Applying the delta onto the base's host-origin directory reproduces the
full merged view at delta cost; full materialization is composition, not a
mode.
One export lease pins the snapshot for the whole run; squash and GC treat it
exactly like any session lease (never mutate or delete a leased layer dir).
dir mode applies the full delta including deletions; a fresh destination
makes deletions no-ops, an existing one converges to the delta's view.
Cost is O(merged delta), never O(image): one metadata fold picks winners,
content is read once per surviving path, zero intermediate trees.
Re-export converges: unchanged winners are skipped by size+mtime, so a
repeated export approaches O(new bytes) with no server-side state.
```

Speed and space, explicitly (the two optimization targets):

| Cost | Bound | Mechanism |
| --- | --- | --- |
| time, enumerate | O(Σ delta-layer entries) | one newest-first metadata fold over non-base layer dirs |
| time, copy | O(merged delta bytes) | winners only — a path overwritten by a newer layer is never read from the older one |
| time, re-export | O(new bytes) | skip-unchanged: copy preserves source mtime, re-run skips equal (size, mtime) |
| space, intermediate | zero | winners stream straight from committed layer dirs; no staging tree |
| space, transport | zstd frame (`tar-zst`) | fewer bytes through the bind mount, where virtiofs latency dominates |
| memory | O(unique changed paths) | the winner map holds path → (layer dir, kind), never content |

`std::fs::copy` already rides `copy_file_range` on Linux (the daemon always
runs in the container, whatever the host OS). Host delivery is the existing
workspace bind: a `dest` under `container_workspace_root` (default
`/workspace`) lands directly in the bound host directory with zero protocol
bytes — the result line is the only thing that crosses the gateway.

## CLI surface

Runtime operation, existing `file` family (published-content domain — no new
family, matching the squash precedent), spec'd in the
`sandbox-runtime-operations` catalog like its siblings:

```text
sandbox-runtime-cli --sandbox-id ID export_changes --dest PATH [--format dir|tar|tar-zst]
```

```text
dest    required, Path.   dir format: destination directory (created if
                          missing). tar formats: destination archive file.
                          Resolved in the daemon's mount namespace; host
                          delivery = a path under the workspace bind root.
                          Rejected under layer_stack_root or scratch_root.
format  optional, String, default "dir".  dir | tar | tar-zst.
```

```rust
pub const EXPORT_CHANGES_SPEC: CliOperationSpec = CliOperationSpec {
    name: "export_changes",
    family: "file",
    summary: "Export the sandbox's published changes to a destination.",
    description: "Fold every published layer above the base (newest-wins, \
                  whiteout/opaque aware) and materialize the delta at --dest \
                  as a directory, or emit it as a whiteout-preserving tar.",
    args: EXPORT_CHANGES_ARGS,
    cli: Some(CliSpec {
        path: &["runtime", "export_changes"],
        usage: "sandbox-runtime-cli --sandbox-id ID export_changes --dest PATH [--format dir|tar|tar-zst]",
        examples: &[
            "sandbox-runtime-cli --sandbox-id ID export_changes --dest /workspace/out",
            "sandbox-runtime-cli --sandbox-id ID export_changes --dest /workspace/delta.tar.zst --format tar-zst",
        ],
    }),
    related: &["file_read", "file_write", "file_edit"],
};

const EXPORT_CHANGES_ARGS: &[ArgSpec] = &[
    ArgSpec::required(
        "dest",
        ArgKind::Path,
        "Destination: directory for dir format, archive file for tar formats.",
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

## Output contract

One compact JSON line on stdout (exit 0); faults are one `{"error":…}` line
on stderr (exit 1). Pretty-printed here.

**dir:**

```json
{
  "manifest_version": 12,
  "format": "dir",
  "layers_exported": ["L000002-…", "S000003-…"],
  "files_written": 214,
  "symlinks_written": 3,
  "deletes_applied": 2,
  "skipped_unchanged": 190,
  "bytes_written": 18874368,
  "live_workspace_sessions": ["ws-7"]
}
```

**tar / tar-zst** (`deletes_applied`/`skipped_unchanged` are dir-mode
concepts and are absent; whiteout winners become archive entries):

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
  "skipped_unchanged": 0,
  "bytes_written": 0
}
```

Counts, not path dumps: the result line stays bounded. Per-path deletion and
skip detail belongs to the observability record (`LAYERSTACK_EXPORT`), the
same division of labor squash uses for byte accounting. `bytes_written` is
regular-file bytes actually copied (dir) or the final archive size (tar).

`live_workspace_sessions` (both formats, omitted when empty — the
`faulty_sessions` precedent) lists the sessions alive at snapshot time.
Their uncommitted upperdir changes are invisible to export by design
(invariant 5); the field is a fact, not a fault — capture/publish and
re-export to include them. Session ids come from the existing session
registry; no upperdir walk, no dirty-check machinery.

Faults: argument problems surface through the existing request-extractor
faults; dest-guard violations and every engine I/O failure are the existing
`operation_failed` — no dedicated error kind:

```json
{"error":{"kind":"operation_failed","message":"export dest inside layer-stack root: …","details":{}}}
```

## Vocabulary and invariants

| Name | Meaning |
| --- | --- |
| delta manifest | The active manifest's layers with every `B*` layer removed — the ordered (newest-first) set of published change layers. Computed from one snapshot; a predicate over `Manifest`, not a new type. |
| export lease | An ordinary `acquire_snapshot` lease taken before the fold and released after the last byte. Its only job is the existing never-mutate-leased guarantee: squash/GC cannot delete a layer dir mid-read. Zero new lease API. |
| winner fold | Pure newest-first fold over the delta layers' entries producing the winner map. Per path, the newest verdict wins: `File{layer_dir}`, `Symlink{layer_dir}`, `Directory`, `Delete` (whiteout winner), with opaque markers cutting all older layers under that directory — the same masking rules `MergedView`/`apply_layer` already encode (`is_kernel_whiteout_meta`, logical `.wh.` prefix, `OPAQUE_MARKER`). Reuses the per-layer walk idiom of `projection/apply.rs`; reads metadata only, never content. |
| winner map | `BTreeMap<LayerPath, Winner>` — O(unique changed paths) memory, deterministic emit order for free. |
| emit-dir | Apply winners onto `dest`: explicit directories via the existing `ensure_directory` semantics (a dest-side symlink or file at a directory position is replaced by a real directory, so writes can never be redirected outside dest), file winners copied then stamped with the source mtime (`File::set_times`), symlink winners recreated, `Delete` winners `remove_path`'d. Skip-unchanged: a file winner whose dest has equal size and mtime is not copied. |
| emit-tar | Stream winners into a `tar::Builder` (workspace `tar` dep) in map order, paths relative. `Delete` winners are emitted in the logical OCI encoding (`.wh.<name>` entry), opaque cuts as `.wh..wh..opq` — never kernel char-dev whiteouts, which need privileges to extract. `tar-zst` wraps the same stream in one zstd encoder. Written to a nonce-named sibling temp file, renamed into place on success. |
| dest guard | `dest` must be absolute and outside `layer_stack_root` and `scratch_root`. dir format: dest must be (or be creatable as) a directory. tar formats: dest must not be an existing directory. |
| skip-unchanged | (size, mtime) equality between a file winner's source and its dest. Sound because emit-dir stamps the source mtime on every copy; stateless because the destination itself carries the watermark. |

Invariants:

1. **Read-only on storage** — export never writes under `layer_stack_root`:
   no staging, no manifest mutation, no sidecars, no substitution state.
2. **Merged-delta equivalence** — emit-dir onto an empty destination equals
   `MergedView` over the delta manifest for every path, including
   directory-only shapes and deletion masking. One fold, one truth.
3. **Lease pins sources** — every layer dir the fold or emit reads is pinned
   by the export lease for the whole run. A concurrent squash sees the
   lease's newest layer as a boundary, exactly like a session lease; a
   concurrent publish prepends layers the snapshot simply doesn't include.
4. **Idempotent re-run** — re-exporting the same manifest version onto the
   same dest writes zero content bytes (all file winners skip) and converges
   to the identical tree.
5. **Published-only** — no session upperdir, no namespace entry, no live
   mount is ever read. The snapshot manifest is the sole source of truth
   (same axis as the file-operations sessionless backend).
6. **Tar atomicity** — the archive is complete-or-absent at `dest`: temp
   name in dest's parent, one rename on success, temp removed on failure.
7. **No durability ceremony** — dir mode does not fsync; a crash mid-export
   leaves a partial dest whose recovery is re-running the export (invariant
   4 makes that cheap). Durability of the host directory is the host's
   concern.

## A. Expected file/folder structure with LoC change

`(new ~N)` = new file with estimated LoC; `(+N)` = lines added to existing
file. Calibrated against existing module sizes (`projection/apply.rs` 157,
`projection/mod.rs` 350, service impls 26–110).

```text
crates/sandbox-runtime/layerstack/
├── src/stack/projection/delta.rs           (new ~140)  delta manifest predicate + winner fold
│                                                       (newest-first, whiteout/opaque masking,
│                                                       metadata-only; shares apply.rs's walk
│                                                       and the whiteout helpers)
├── src/stack/projection/emit_dir.rs        (new ~120)  apply winner map onto dest: ensure-dir
│                                                       semantics, copy + mtime stamp,
│                                                       skip-unchanged, delete winners, counts
├── src/stack/projection/emit_tar.rs        (new ~110)  winner map → tar stream, logical .wh.
│                                                       re-encoding, optional zstd, temp+rename
├── src/stack/projection/mod.rs             (+15)       exports; walk visibility pub(super)→
│                                                       shared within projection
└── tests/unit/{export_delta.rs (new ~180), export_emit.rs (new ~220)} · tests/unit.rs (+2)

crates/sandbox-runtime/operation/
├── src/layerstack/service/impls/export_changes.rs (new ~90)
│                                                       the whole transaction: dest guard →
│                                                       acquire_snapshot lease → delta → fold →
│                                                       emit by format → result assembly →
│                                                       release lease (guard-scoped)
├── src/layerstack/service/{model,mod}.rs   (+25)       ExportFormat, ExportOutcome DTOs, export
├── src/cli_definition/file_operations.rs   (+45)       dispatch_export_changes + entry in the
│                                                       existing file group (operation.rs +0:
│                                                       no new entry group, no new mechanism)
└── tests/layerstack_export.rs (new ~150)   end-to-end dispatch: dir, tar, empty delta,
                                            dest-guard faults, re-run convergence

crates/sandbox-runtime-operations/
├── src/export.rs                           (new ~70)   EXPORT_CHANGES_SPEC + args
└── src/lib.rs                              (+5)        catalog registration

crates/sandbox-observability/
└── src/record.rs                           (+3)        LAYERSTACK_EXPORT

Cargo.toml (workspace)                      (+1)        zstd
crates/sandbox-runtime/layerstack/Cargo.toml (+2)       tar.workspace, zstd.workspace

sandbox-protocol / sandbox-daemon / sandbox-gateway / sandbox-config   (+0)
```

Totals: **5 new source files ≈ 530 LoC**, **≈ +95 LoC** in existing files,
**≈ 550 LoC** of tests → ≈ 1,180 LoC end to end. No protocol change, no
daemon change, no config field, no new operation-entry mechanism, no new
family.

Build order: winner fold (pure) → emit-dir → emit-tar → service impl +
catalog spec + dispatcher → observability record.

## B. Export workflows

Legend: `Ln` published layer, `B` base, `wh(p)` whiteout of path p,
`opq(d)` opaque marker on directory d. Manifests are newest-first.

### B1. Simple — two layers onto a fresh directory

```text
active v3: [L2 L1 B]        L1: src/a.rs, src/b.rs      L2: src/a.rs (edit), wh(src/b.rs)

fold (newest-first):
  src/a.rs → File(L2)        L1's a.rs is masked: never read
  src/b.rs → Delete          whiteout wins
  src/     → Directory

emit-dir --dest /workspace/out (fresh):
  out/src/a.rs written (L2 content, L2 mtime), delete of src/b.rs = no-op

result: files_written 1, deletes_applied 0, skipped_unchanged 0
```

The base's thousands of files never enter the fold; cost is two layer walks
plus one file copy.

### B2. Re-export after more publishes — incremental by property

```text
first export @v3 to /workspace/out          214 files copied
publishes land: v5 = [L4 L3 L2 L1 B]        L3, L4 touch 9 paths
second export @v5 to the same dest:
  205 winners: equal (size, mtime) at dest → skipped
  9 winners: copied
result: files_written 9, skipped_unchanged 205
```

No watermark flag, no server state: the mtime stamp written at copy time is
the watermark, carried by the destination itself.

### B3. Masking — opaque directory and nested whiteouts

```text
L1: cfg/dev.yml, cfg/prod.yml    L2: opq(cfg), cfg/prod.yml (rewrite)

fold: cfg → Directory, cfg/prod.yml → File(L2)
      cfg/dev.yml never becomes a winner (opaque cut masks L1 under cfg)

emit-dir onto a dest that already has cfg/dev.yml from an older export:
  the opaque cut re-emits as Delete winners for masked dest survivors? NO —
  the fold emits exactly the merged view; dest convergence for paths that
  left the delta between exports is out of scope (the delta no longer
  describes them). Converging a stale dest = export to a fresh dir, or
  re-run after squash which re-emits the surviving shape.
```

The contract is "dest reflects this delta", not "dest is synchronized with
delta history" — stated here so nobody retrofits rsync semantics later.

### B4. Archive — tar-zst with whiteouts preserved

```text
same v3 as B1, --format tar-zst --dest /workspace/delta.tar.zst

entries: src/ · src/a.rs · src/.wh.b.rs        (logical OCI encoding)
write: /workspace/.delta.tar.zst.<nonce> → rename → delta.tar.zst

result: files_written 1, whiteouts_emitted 1, bytes_written = archive size
```

The archive is a valid OCI-style layer: lossless (deletions survive),
transportable, re-importable, and compressed before it crosses the
virtiofs bind mount.

### B5. Concurrent squash — the lease does all the work

```text
export starts @v13, lease snapshot [L12 L11 L10 … B]
checkpoint_squash runs mid-export:
  export lease's newest layer is a boundary → blocks straddling it squash
  around it; replaced layers the export still reads stay on disk (never
  mutate/delete a leased layer dir — existing law, no new code)
export completes on its snapshot, reports manifest_version 13
lease releases → refcount GC reclaims whatever only the export pinned
next squash compacts what this one had to skip
```

No retry, no invalidation, no special case: invariant 3 is the whole story.

## C. Non-goals and deferrals

- **Live-session export** — upperdirs are captured/published by the existing
  session lifecycle; export reads published truth only and reports live
  sessions in the result line rather than failing on them. Publish first.
- **Full materialization (`dir-full`)** — a self-contained snapshot
  including the base, for a dest with no host copy of the seed or one that
  has diverged. The primitive already exists (`MergedView::project`: dest
  wipe + oldest-first apply of ALL layers); exposing it is one new `format`
  value whenever the need materializes. Deliberately not v1: it costs
  O(image) per run, forces a dest wipe (full-view deletions are absences,
  which only converge from empty), and defeats skip-unchanged — the delta
  plus the host's own base copy reproduces the same view at O(delta).
- **Bounded parallel copy** — a width-N worker pool over file winners
  (squash's remount-sweep precedent, default 4, env-tunable) is deferred to
  a measured experiment on the virtiofs bind path; the winner fold already
  removes the redundant-byte cost, and serial emit keeps v1 at one code
  path. Follow the squash perf-experiment format under
  `export_changes/experiments/` when taken up.
- **Streaming export over the gateway** — for sandboxes created without a
  workspace bind root. Would add a paging op (exec_command/read_lines
  precedent) and is the only piece that would ever touch sandbox-protocol.
  Out of scope until such a deployment exists.
- **Byte-level deltas between exports** — skip-unchanged captures the win at
  a fraction of the complexity.
- **Path filtering, dry-run, checksum flags** — deliberately absent; the
  operation has two arguments. Per-layer digests in `.layer-metadata/`
  remain the integrity story.

## Decision log

1. **Two arguments only** (user review, 2026-07-07): `--dest`, `--format`.
   Incrementality, deletion policy, parallelism, and scope became
   defaults/properties instead of flags.
2. **Deletions apply by default in dir mode**: "convert the changes"
   includes deletions; a fresh dest makes them no-ops; counts in the result
   line, per-path detail in the observability record (bounded result,
   squash precedent — supersedes the chat sketch's itemized list).
3. **Family `file`, no new family**; dispatcher joins the existing file
   group so `operation.rs` is untouched.
4. **Reuse over invention**: winner fold lives beside `projection/apply.rs`
   and shares its walk and whiteout vocabulary; `MergedView` stays the
   read-path truth; equivalence between the two is invariant 2 and a unit
   test, not a shared abstraction forced before it's needed.
5. **mtime-stamped copies** power skip-unchanged (`File::set_times`,
   rust ≥ 1.75; workspace floor is 1.85) — chosen over a server-side
   watermark to keep the daemon stateless.
6. **`zstd` enters `[workspace.dependencies]`** for `tar-zst`; `tar` is
   already there. Compression is worth a dependency because bind-mount
   write bytes are the dominant cost on Docker Desktop.
7. **Logical `.wh.` encoding in archives** (kernel char-dev whiteouts need
   privileged extraction); emit-dir consumes whiteouts directly and never
   writes them to dest.
8. **No fsync in dir mode**: idempotent re-run is the recovery story;
   tar mode gets temp+rename because a partial archive is corrupt, not
   merely incomplete.
9. **Delta, not full materialization** (design review, 2026-07-07): the
   base seed is host-origin, so a full export re-copies bytes the host
   already has at O(image) and forces a dest wipe every run; the delta
   yields the full view by composition onto the host's base copy at
   O(delta) and is the only form that carries deletions explicitly.
   `dir-full` stays a documented deferral backed by the existing
   `MergedView::project`.
10. **Running sessions report, never fail** (design review, 2026-07-07):
    session existence is not evidence of missing changes, sessions never
    mutate published layers (no consistency hazard to guard), and blocking
    export under long-lived sessions would make it unusable. Silent
    omission is equally wrong, so the result line carries
    `live_workspace_sessions` — the squash report-don't-fail precedent.
