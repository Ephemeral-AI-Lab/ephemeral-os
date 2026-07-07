---
title: Manager Export Changes — Live-Docker E2E Test Catalog
tags:
  - ephemeral-os
  - layerstack
  - manager
  - export
  - testing
status: draft
updated: 2026-07-07
---

# Manager Export Changes — live e2e catalog (30 cases)

Companion to `spec.md` (same folder — design truth) and `adversarial-review-prompt.md`
(the hardening axes). This document defines the **live Docker sandbox**
catalog that drives real published layer stacks through
`sandbox-manager-cli export_changes`, applies the delta onto a **host**
destination, and asserts on structured JSON and the resulting on-disk tree —
never log scraping. **10 easy (EZ), 10 medium (MED), 10 hard (HRD).**

Export is a **manager** operation, so cases live beside the squash suite in
`cli-operation-e2e-live-test/manager/management/export/`, one file per tier
(`test_export_easy.py` / `test_export_medium.py` / `test_export_hard.py`,
markers `export and easy` / `export and medium` / `export and hard`).
They reuse:

- `manager/management/helpers.py` — `create_sandbox`, `inspect_sandbox`,
  `destroy_sandbox`, `get_observability_tree`.
- `manager/management/squash/helpers.py` — publish-churn scenarios
  (`file_write`/exec/session publishes), `checkpoint_squash`, layerstack
  observability, deep-stack builders.
- a new `manager/management/export/helpers.py` — `export_changes(sandbox_id,
  dest, format="dir")`, `read_tree(dest)`, `assert_delta_equivalence`,
  `assert_result_contract`, `zstd_entries(path)`, `sentinel_guard(outside)`,
  and the fault-injection primitives (`inject_spool`, `hostile_daemon`) the
  host-boundary tier needs.

Every executed case writes
`manager/management/export/test-reports/<RUN_ID>/<CASE_ID>/verdict.json`.

> **Runnability.** The design is a spec, not yet code. Every case drives the
> product surface (`sandbox-manager-cli export_changes`), so the catalog
> becomes runnable when the operation and the `sandbox-manager/src/export_apply.rs`
> applier land (spec §A build order). The Rust unit suite
> (`sandbox-manager/tests/manager_export.rs` — fake **and** hostile daemon)
> is the fast gate and is not repeated here; this catalog is the
> live-environment suite. Host-boundary cases (HRD-01…05, HRD-10) that need a
> *malicious* stream run against a **fault-injecting daemon build** or a
> **pre-crafted spool** dropped into the daemon's `<scratch_root>/.export/`
> (§1.4) — the honest daemon cannot author a traversal entry, and pretending
> otherwise would make those cases vacuous.

## The contract under test

Export folds every published layer above the base (newest-wins,
whiteout/opaque aware) into one zstd tar delta, streams it over the daemon
protocol in bounded chunks, and the **manager** renders it host-side. The
catalog pins the spec's ten invariants plus the four adversarial axes:

1. **Delta equivalence.** The `dir` apply onto an empty dest, and the
   `tar`/`tar-zst` archive re-applied onto a base copy, equal `MergedView`
   over the delta manifest for every path — winners, deletions, opaque cuts,
   directory-only shapes (spec inv 2).
2. **Reproduces the merged view onto the seed.** Applied onto the host
   directory the base was seeded from, the result **is** the sandbox's full
   merged view at O(delta) cost — the base never crosses the wire (spec B1,
   cost table).
3. **The host boundary holds.** The applier is a host process with operator
   privileges consuming sandbox-authored tar; a compromised daemon is in
   scope. No entry escapes dest — `..`, absolute, hardlink, symlink-then-
   traverse, and out-of-dest whiteout targets are all rejected; the dest
   deny-list holds; resource bombs are capped (spec inv 9; adversarial C1,
   HRD-04/05).
4. **Ordering is load-bearing.** Opaque clears run before the directory's
   children so a dotfile winner is never destroyed (spec inv 2; adversarial
   C2, MED-04).
5. **Incremental by property.** Re-run writes zero *content* bytes for file
   winners via (size, second-mtime) skip; deletions/opaque clears re-apply
   and their counts are non-zero (spec inv 4, MED-01).
6. **Fidelity boundary is honest.** Mode and symlink targets carry; uid/gid,
   xattrs, and cross-winner hardlinks do not; `.wh.`-prefixed filenames are
   unrepresentable upstream so the deletion encoding is unambiguous (spec
   inv 10; `stack/publish/route.rs:22-33`).
7. **Report, never fail, on live sessions.** A running session names itself
   in `live_workspace_sessions` and export still succeeds on published state
   (spec §output contract, decision 10).
8. **Lease + snapshot consistency.** Concurrent squash/publish never tears an
   export; the export delivers its snapshot's delta exactly (spec inv 3, B5).

### Decision table (the heart of the catalog)

| Scenario | Expected outcome | Case |
| --- | --- | --- |
| edit + delete, `dir` onto the seed | dest == merged view; `a.rs` rewritten, `b.rs` gone | EZ-01 |
| base-only manifest, `dir` | no-op; `layers_exported == []`; all counts 0; dest untouched | EZ-02 |
| delta, `--format tar-zst` | valid zstd archive; `.wh.b.rs` present; `whiteouts_emitted == 1` | EZ-03 |
| delta, `--format tar` | valid plain (non-zstd) tar of the same entries | EZ-04 |
| `--format` omitted | behaves as `dir` | EZ-05 |
| relative `--dest` | manager-side reject, no forward | EZ-06 |
| dir result JSON | exact keys, integer counts, `format == "dir"` | EZ-07 |
| single deletion, `dir` | file removed; **no literal `.wh.b.rs` written to dest** | EZ-08 |
| export with a live session | `ok`; `live_workspace_sessions:["ws-…"]`; omitted when none | EZ-09 |
| export a non-Ready sandbox | forward-gate reject; dest untouched | EZ-10 |
| re-export same version, same dest | `files_written 0`; `skipped_unchanged == entries` | MED-01 |
| re-export after +2 layers touching 9 paths | `files_written 9`; rest skipped | MED-02 |
| `opq(cfg)` + rewrite | `cfg/dev.yml` cleared, `prod.yml` rewritten; `opaque_clears 1` | MED-03 |
| `opq(cfg)` + winner `cfg/.env` | `.env` **survives** the clear (ordering) | MED-04 |
| `a.rs` edited in L1 then L2 | only L2 content exported; L1 never read | MED-05 |
| symlink winner; dir↔symlink swap | recreated, never followed | MED-06 |
| `dir` onto **empty** dest | tree == `MergedView` over the delta | MED-07 |
| big base, small delta | `bytes_written ≈ delta`, not image | MED-08 |
| `chmod`ed file/dir | mode carried; uid/gid + xattrs not | MED-09 |
| `tar-zst` re-applied onto a fresh base copy | equals merged view; deletions survive | MED-10 |
| entry name with `..`/absolute | rejected; sentinel outside dest untouched | HRD-01 |
| symlink `x→/etc` then `x/passwd` | write-through blocked; sentinel untouched | HRD-02 |
| `.wh.` target normalizing outside dest | rejected; no out-of-dest deletion | HRD-03 |
| `--dest /`, `$HOME`, manager state dir | deny-list reject, pre-forward | HRD-04 |
| zstd bomb / entry-count bomb | capped; abort without disk exhaustion | HRD-05 |
| two concurrent exports, same sandbox | singleflight; `export_id`-keyed spools; no cross-corruption | HRD-06 |
| export during `checkpoint_squash` | lease pins sources; both converge | HRD-07 |
| export during a publish | snapshot excludes new layers | HRD-08 |
| 499-layer / ~1 GB delta | converges, or clean start-request-ceiling fail | HRD-09 |
| daemon restart mid-paging | `export-not-found` abort → re-run converges; `.export/` reaped | HRD-10 |

---

## 1. Environment & fixture toolkit

### 1.1 Bring-up

```sh
export PATH="$PWD/bin:$PATH"
bin/start-sandbox-docker-gateway --rebuild-binary

RUN_ID=export-$(date +%Y%m%d-%H%M%S)
```

One sandbox per case unless the case is explicitly multi-actor (HRD-06…08).
Serial by default. Base content is seeded host-side via the bind-root
workspace variant (`config.workspace_variant("export_seed")` under `repo/`);
the delta is produced by publish churn (runtime sessions/execs or sessionless
`file_write`) reusing the squash suite's scenario builders. For the **primary**
workflow (B1) `--dest` is the same host bind-root the base was seeded from.

**Environment preconditions — asserted once per suite, hard-fail (never skip):**

| # | Precondition | Check | Expected |
| --- | --- | --- | --- |
| P1 | `export_changes` is in the manager catalog | `sandbox-manager-cli export_changes --help` (or catalog list) | usage lists `--sandbox-id`, `--dest`, `--format`; **and** a `SPECS↔OPERATIONS` parity assertion passes (spec H6) |
| P2 | zstd round-trip is available host-side | export `tar-zst`, then `zstd -t` / `tar tf` the archive | archive decompresses; entries list |
| P3 | dir-apply onto the bind-root seed is reachable | export `dir` onto the seed dir, read one winner back | winner byte-equal; the post-base bind detach did not block the manager host write |
| P4 | export boot step reaps `.export/` | drop a sentinel `<scratch_root>/.export/orphan.tar.zst`, restart the daemon, list scratch | sentinel gone (spec H1); the session reap alone would leak it |

P3 pins spec invariant 6's "manager is the only host writer" as a live fact.
P4 pins the boot-reap correction before HRD-10 depends on it.

### 1.2 Fixture vocabulary

| Fixture | Meaning | Construction |
| --- | --- | --- |
| `seed(F)` | sandbox whose base holds files `F`, exportable onto its own seed dir | bind-root variant written with `F`, then `create_sandbox` |
| `delta(...)` | one or more published layers above the base | session finalize / one-shot exec publish / `file_write`, squash-suite builders |
| `dest_fresh` | an empty host dir (no base copy) | `tmp_path` subdir — exercises the sparse-tree / equivalence path |
| `dest_seed` | the bind-root seed dir itself | the B1 in-place-override target |
| `dest_archive` | a host archive path + nonce `.tmp` sibling | for `tar`/`tar-zst` |
| `sentinel(outside)` | a canary file/dir **outside** dest | proves no host-boundary escape (HRD tier) |
| `hostile` | a fault-injecting daemon or a crafted spool | authors traversal/bomb entries the honest daemon cannot (§1.4) |
| `squashed` | stack after `checkpoint_squash` | squash-suite pattern |

### 1.3 Teardown contract (part of every case)

After each case, in order — a teardown failure **fails the case loudly**:

1. Destroy every session; destroy the sandbox last.
2. `observability layerstack` shows `active_lease_count == 0` on every layer
   (the export lease released; spec inv 3).
3. `<scratch_root>/.export/` is empty (every spool unlinked on eof or reaped;
   spec §daemon ops).
4. No host path **outside** dest was created, modified, or deleted (compare
   `sentinel` and the dest's parent tree; the load-bearing teardown for the
   HRD tier).
5. Artifact bundle written even on failure (`cmd.log`, `result.json`,
   `layerstack.json`, dest tree manifest, per-case `verdict.json`).

---

## 2. Measurement — the three axes

Every case is verified on three axes; it passes only when all three pass (a
case may mark an axis `n/a`).

1. **Correctness (delta & contract)** — the exported tree/archive equals the
   expected merged delta (`assert_delta_equivalence` against a `MergedView`
   materialization or a known-good fixture), and the result JSON is exact:
   `manifest_version`, `layers_exported`, and every count
   (`files_written`, `symlinks_written`, `deletes_applied`, `opaque_clears`,
   `skipped_unchanged`, `bytes_written`, or the `tar` set with
   `whiteouts_emitted`) match the constructed delta.
2. **Host-boundary safety (load-bearing)** — nothing outside dest is touched;
   hostile entries are rejected with a structured error; the dest deny-list
   holds; `.wh.`/opaque markers are consumed, never written literally into a
   `dir` dest; resource caps hold. Happy-path cases assert only the
   "no literal marker / nothing outside dest" half.
3. **Incrementality & idempotence** — re-run writes zero content bytes for
   file winners; `skipped_unchanged` equals the unchanged entry count;
   `bytes_written` tracks delta, not image; a partially applied dest
   converges on re-run.

Per-case `verdict.json` (one schema for all 30):

```json
{
  "case_id": "EZ-01",
  "run_id": "export-20260707-120000",
  "status": "pass",
  "axes": {
    "correctness":  { "pass": true, "manifest_version": 3, "counts_match": true, "equivalence": "MergedView" },
    "host_safety":  { "pass": true, "outside_dest_touched": false, "literal_markers": 0, "rejected_class": null },
    "incremental":  { "pass": true, "content_bytes_written": 512, "skipped_unchanged": 0 }
  },
  "teardown": { "pass": true, "lease_registry_empty": true, "export_dir_empty": true, "outside_dest_clean": true },
  "defects": []
}
```

---

## 3. Test catalog

Per-case format: **Spec** (invariant/workflow) · **Fixture** · **Steps** ·
**Correctness** · **Host-safety** · **Incremental**. Every case ends with the
§1.3 teardown contract; it is not repeated.

### 3.1 Easy — EZ-01…10

Single delta, one behavior each. Whole tier is the rebuild gate
(`pytest -m "export and easy"`).

#### EZ-01 — dir export onto the seed reproduces the merged view (THE primary, B1)
- **Spec**: B1, inv 2. **Fixture**: `seed({"src/a.rs":"v1\n","src/b.rs":"B\n"})`; `delta`: publish `src/a.rs="v2\n"` + delete `src/b.rs`. **Dest**: `dest_seed`.
- **Steps**: `export_changes(sid, dest_seed)` → read `src/a.rs`, `src/b.rs` on the host tree.
- **Correctness**: `src/a.rs == "v2\n"`, `src/b.rs` absent; result `files_written 1`, `deletes_applied 1`, `symlinks_written 0`, `opaque_clears 0`, `manifest_version` == the published version, `layers_exported` names the delta layers.
- **Host-safety**: no literal `.wh.b.rs` on the host; nothing outside `dest_seed`.
- **Incremental**: n/a (first export).

#### EZ-02 — base-only manifest is a clean no-op
- **Spec**: §output contract (empty delta). **Fixture**: `seed({"keep.txt":"K\n"})`, no publishes. **Dest**: `dest_seed`.
- **Steps**: `export_changes` → snapshot the dest tree before/after.
- **Correctness**: `layers_exported == []`; every count 0; `manifest_version == 1`; no `no_op` flag (state speaks for itself).
- **Host-safety**: dest byte-identical before and after; nothing outside dest.
- **Incremental**: n/a.

#### EZ-03 — tar-zst writes a valid whiteout-preserving archive (B4)
- **Spec**: B4, decision 7. **Fixture**: the EZ-01 delta. **Dest**: `dest_archive` `/tmp/$RUN_ID/delta.tar.zst`.
- **Steps**: `export_changes(..., format="tar-zst")` → `zstd_entries(dest)`.
- **Correctness**: archive is zstd (magic `0x28 B5 2F FD`); entries `src/`, `src/a.rs`, `src/.wh.b.rs` (logical OCI encoding); result `whiteouts_emitted 1`, `files_written 1`, `bytes_written == os.path.getsize(dest)`; apply-side fields absent.
- **Host-safety**: only `dest` and its nonce `.tmp` sibling appear/disappear; no `.tmp` left behind (atomicity, inv 7).
- **Incremental**: n/a.

#### EZ-04 — tar writes a plain (decompressed) archive
- **Spec**: `--format tar` rendering. **Fixture**: EZ-01 delta. **Dest**: `/tmp/$RUN_ID/delta.tar`.
- **Steps**: `export_changes(..., format="tar")` → `tar tf dest`.
- **Correctness**: not zstd magic; `tar tf` lists the same logical entries as EZ-03; counts equal EZ-03's.
- **Host-safety**: as EZ-03.
- **Incremental**: n/a.

#### EZ-05 — `--format` defaults to dir
- **Spec**: CLI surface default. **Fixture**: EZ-01 delta. **Dest**: `dest_fresh`.
- **Steps**: `export_changes` with no `--format` → assert applied as a directory tree, not an archive file.
- **Correctness**: result `format == "dir"`; dest is a directory containing `src/a.rs`; no archive bytes.
- **Host-safety**: no literal markers.
- **Incremental**: n/a.

#### EZ-06 — relative `--dest` is rejected before any forward
- **Spec**: dest guard (absolute-only). **Fixture**: EZ-01 delta.
- **Steps**: invoke with `--dest ./relative` → inspect structured error; assert no daemon forward happened (`observability` shows no export lease acquired).
- **Correctness**: `{"error":{...}}` with the manager error kind; exit 1; `manifest_version` absent.
- **Host-safety**: nothing written anywhere; `.export/` empty (fold never started).
- **Incremental**: n/a.

#### EZ-07 — dir result-contract shape is exact
- **Spec**: §output contract (dir). **Fixture**: EZ-01 delta. **Dest**: `dest_fresh`.
- **Steps**: `export_changes` → schema-assert the JSON.
- **Correctness**: keys exactly `{manifest_version, format, layers_exported, files_written, symlinks_written, deletes_applied, opaque_clears, skipped_unchanged, bytes_written}` (+ `live_workspace_sessions` only when non-empty); every count a non-negative integer; no path dumps.
- **Host-safety**: n/a. **Incremental**: n/a.

#### EZ-08 — a single deletion applies in dir mode with no literal marker (marker purity)
- **Spec**: inv 10, decision 7. **Fixture**: `seed({"gone.txt":"X\n"})`; `delta`: delete `gone.txt`. **Dest**: `dest_seed`.
- **Steps**: `export_changes(dir)` → `ls -a` the dest.
- **Correctness**: `gone.txt` absent; `deletes_applied 1`, `files_written 0`.
- **Host-safety**: **no `.wh.gone.txt` file exists on the host** — the applier consumed the whiteout; nothing outside dest.
- **Incremental**: n/a.

#### EZ-09 — a live session is reported, export still succeeds (report-not-fail)
- **Spec**: decision 10, `faulty_sessions` precedent. **Fixture**: `seed({...})` + EZ-01 delta **published**, then open a live session (unfinalized). **Dest**: `dest_fresh`.
- **Steps**: `export_changes` with the session alive → then destroy the session and export again.
- **Correctness**: first result `ok` with `live_workspace_sessions:["ws-…"]` naming the live session; exported content is the **published** state only (the session's unpublished upperdir is invisible, inv 5); second result **omits** `live_workspace_sessions`.
- **Host-safety**: n/a. **Incremental**: n/a.

#### EZ-10 — a non-Ready sandbox is rejected by the forward gate
- **Spec**: forward Ready gate. **Fixture**: a sandbox id in a non-Ready state (freshly `create` still `creating`, or a `stopping` id), or a bogus id.
- **Steps**: `export_changes` → structured error.
- **Correctness**: `error.kind` is the invalid-state/not-found class; no `manifest_version`.
- **Host-safety**: dest untouched; `.export/` empty.
- **Incremental**: n/a.

### 3.2 Medium — MED-01…10

One interaction dimension per case.

#### MED-01 — idempotent re-run writes zero content bytes (inv 4)
- **Spec**: inv 4, B2. **Fixture**: EZ-01 delta. **Dest**: `dest_seed`.
- **Steps**: `export_changes` twice onto the same dest, no publishes between.
- **Correctness**: second result `files_written 0`; `skipped_unchanged` == the file-entry count; `manifest_version` identical.
- **Host-safety**: no literal markers; nothing outside dest.
- **Incremental**: `content_bytes_written == 0` on run 2; the dest tree is byte-identical between runs (file winners); `deletes_applied` may be non-zero (re-applied) — assert the count is reported, not that it is zero.

#### MED-02 — incremental re-export after more publishes (B2, skip watermark)
- **Spec**: B2, skip-unchanged. **Fixture**: EZ-01 delta exported at `@v_a`; then publish 2 more layers touching 9 distinct paths. **Dest**: `dest_seed`.
- **Steps**: re-`export_changes` at `@v_b`.
- **Correctness**: `files_written == 9`; `skipped_unchanged == prior_entries`; `manifest_version == v_b`; the 9 paths carry the new content.
- **Host-safety**: no markers; nothing outside dest.
- **Incremental**: `content_bytes_written` ≈ the 9 changed files' bytes, independent of the unchanged set.

#### MED-03 — opaque directory masks base content (B3)
- **Spec**: B3, inv 2. **Fixture**: `seed({"cfg/dev.yml":"D\n","cfg/prod.yml":"P\n"})`; `delta`: `opq(cfg)` + rewrite `cfg/prod.yml="P2\n"`. **Dest**: `dest_seed`.
- **Steps**: `export_changes(dir)` → read `cfg/dev.yml`, `cfg/prod.yml`, `ls cfg`.
- **Correctness**: `cfg/dev.yml` **absent** (opaque-cleared base-origin file), `cfg/prod.yml == "P2\n"`; result `opaque_clears 1`, `files_written 1`.
- **Host-safety**: no `.wh..wh..opq` file left on the host; nothing outside dest.
- **Incremental**: n/a.

#### MED-04 — opaque-clear ordering: a dotfile winner survives the clear (inv 2 / adversarial C2)
- **Spec**: inv 2 (the C2 regression). **Fixture**: `seed({"cfg/dev.yml":"D\n"})`; `delta`: `opq(cfg)` + winners `cfg/.env="E\n"` and `cfg/prod.yml="P\n"`. **Dest**: `dest_seed`.
- **Steps**: `export_changes(dir)` → read `cfg/.env`, `cfg/prod.yml`, `cfg/dev.yml`.
- **Correctness**: `cfg/.env == "E\n"` **and** `cfg/prod.yml == "P\n"` (both winners survive); `cfg/dev.yml` absent. A blind tar-order applier writes `cfg/.env` (sorts before `cfg/.wh..wh..opq`) then clears it — this case fails on that bug and passes only when the applier runs opaque-clear-then-content per directory.
- **Host-safety**: no literal opaque marker; nothing outside dest.
- **Incremental**: n/a.

#### MED-05 — newest-wins winner fold: older layer content is never exported
- **Spec**: winner fold ("winners only"). **Fixture**: `seed({})`; `delta`: L1 `a.rs="v1\n"`, then L2 `a.rs="v2\n"`. **Dest**: `dest_fresh`.
- **Steps**: `export_changes(dir)` → read `a.rs`; inspect `observability` `LAYERSTACK_EXPORT` for read accounting where available.
- **Correctness**: `a.rs == "v2\n"`; `files_written 1` (one winner, not two); `layers_exported` may list both layers but only the newer `a.rs` content crosses.
- **Host-safety**: n/a.
- **Incremental**: `content_bytes_written` == v2 size only (the masked v1 was never read — cost table "winners only").

#### MED-06 — symlink winner recreate; directory↔symlink replacement (adversarial M5)
- **Spec**: host apply symlink semantics, inv 9. **Fixture**: `seed({"link_target/keep.txt":"K\n"})`; `delta`: a winner symlink `s -> link_target`, and a winner **directory** `d` at a path where `dest_seed` currently holds a **symlink** `d -> elsewhere`. **Dest**: `dest_seed` pre-loaded with the conflicting symlink.
- **Steps**: `export_changes(dir)`.
- **Correctness**: `s` is a symlink with target `link_target`; `d` is now a real directory (the dest symlink was replaced, never followed); `symlinks_written 1`.
- **Host-safety**: the pre-existing `d -> elsewhere` symlink's target directory is **untouched** — replacement did not write through it; nothing outside dest.
- **Incremental**: n/a.

#### MED-07 — merged-delta equivalence vs `MergedView` on an empty dest (inv 2)
- **Spec**: inv 2. **Fixture**: a delta mixing files, a symlink, a deletion, and an opaque cut. **Dest**: `dest_fresh`.
- **Steps**: `export_changes(dir)` → build the reference by projecting the same delta manifest via a known-good materialization (a full clone + `MergedView`, or the squash flatten of the delta layers) → diff trees.
- **Correctness**: every path (content, mode, symlink target, presence/absence) equal to the reference; directory-only shapes present.
- **Host-safety**: no markers; nothing outside dest.
- **Incremental**: n/a.

#### MED-08 — delta-cost: the base never crosses the wire
- **Spec**: cost table, decision 9. **Fixture**: `seed` with a large base (e.g. 2,000 files / tens of MiB) and a **1-file** delta. **Dest**: `dest_fresh`.
- **Steps**: `export_changes(dir)` → measure `bytes_written` and the daemon `spool_bytes`.
- **Correctness**: `files_written 1`; `bytes_written` ≈ the single winner's bytes; `spool_bytes` ≪ base size.
- **Host-safety**: n/a.
- **Incremental**: `content_bytes_written` is O(delta), not O(image) — the base bytes the host already owns are not re-copied.

#### MED-09 — metadata fidelity: mode carried, uid/gid + xattrs not (inv 10)
- **Spec**: inv 10. **Fixture**: `delta` publishing a file `chmod 0640` and a directory `chmod 0700`, plus a user xattr on a file where the platform allows. **Dest**: `dest_fresh`.
- **Steps**: `export_changes(dir)` → `stat` the file and dir; read xattrs.
- **Correctness**: file mode `0640`, dir mode `0700` (carried); uid/gid equal the **manager process** owner, not the sandbox's (not carried); the user xattr is **absent** (documented boundary, not a defect).
- **Host-safety**: n/a.
- **Incremental**: n/a.

#### MED-10 — tar-zst archive re-applies onto a fresh base copy (portability, B4)
- **Spec**: B4. **Fixture**: EZ-01 delta exported as `tar-zst`; a **second** host dir seeded with the same base (`cp -a` of the seed). **Dest**: the archive.
- **Steps**: apply the archive onto the second base copy (via the applier's archive-apply path or a helper that runs the same logical apply) → read the result.
- **Correctness**: the second dir now equals the sandbox merged view — `src/a.rs=="v2\n"`, `src/b.rs` gone (the logical whiteout applied as a delete on a different base copy).
- **Host-safety**: nothing outside the second dir.
- **Incremental**: n/a.

### 3.3 Hard — HRD-01…10

Adversarial host boundary, concurrency, scale, failure. These carry the
catalog's weight (adversarial Axis 4 outranks the rest).

#### HRD-01 — tar-slip: `..`/absolute entry is rejected, nothing escapes dest (adversarial C1, inv 9)
- **Spec**: inv 9. **Fixture**: `hostile` spool with an entry named `../../escape.txt` and one with an absolute name `/tmp/escape.txt`; `sentinel` files at both target paths outside dest. **Dest**: `dest_fresh`.
- **Steps**: run the manager apply over the crafted stream.
- **Correctness**: export aborts with a structured error naming the rejected entry; no valid entries from that stream partially applied (or only in-dest entries applied, per the spec's rejection granularity — pinned at implementation).
- **Host-safety** (load-bearing): both sentinels **byte-identical** afterward; no file created outside `dest_fresh`; the parent-dir tree is unchanged.
- **Incremental**: n/a.

#### HRD-02 — symlink-then-traverse: write-through is blocked (adversarial C1, inv 9)
- **Spec**: inv 9 (O_NOFOLLOW fd-walk). **Fixture**: `hostile` spool: entry 1 a symlink `x -> /tmp/evil_dir`, entry 2 a file `x/passwd`; `sentinel` `/tmp/evil_dir/passwd` absent, `/tmp/evil_dir` pre-created and empty. **Dest**: `dest_fresh`.
- **Steps**: apply.
- **Correctness**: the applier either rejects the second entry or replaces `x` with a real directory in-dest; `dest_fresh/x/passwd` may exist **inside dest**, but…
- **Host-safety** (load-bearing): `/tmp/evil_dir/passwd` is **never created** — no write followed the symlink out of dest; `/tmp/evil_dir` stays empty.
- **Incremental**: n/a.

#### HRD-03 — whiteout target normalizing outside dest is rejected (inv 9)
- **Spec**: inv 9 (validate after `.wh.` prefix strip). **Fixture**: `hostile` spool with a whiteout entry whose logical target resolves to `../../victim`; `sentinel` `victim` outside dest with known content. **Dest**: `dest_fresh`.
- **Steps**: apply.
- **Correctness**: structured reject naming the offending whiteout; no `remove_path` runs outside dest.
- **Host-safety** (load-bearing): the outside-dest `victim` is **still present and byte-equal** — the deletion never escaped.
- **Incremental**: n/a.

#### HRD-04 — dest deny-list holds (inv 9 / adversarial L1)
- **Spec**: dest guard deny-list. **Fixture**: honest delta. **Dests** (parametrized): `/`, `$HOME`, the manager state/registry dir, a path inside `<scratch_root>/.export/`.
- **Steps**: `export_changes(dir)` against each → structured error **before** any forward.
- **Correctness**: each returns the deny-list manager error; no daemon fold started (`.export/` empty; no lease).
- **Host-safety** (load-bearing): none of the denied roots is modified — spot-check that `/` and `$HOME` are untouched.
- **Incremental**: n/a.

#### HRD-05 — resource bombs are capped (inv 9)
- **Spec**: inv 9 (decompression + entry-count caps, untrusted daemon counts). **Fixture**: (a) `hostile` `zstd bomb` — a few-MiB spool inflating to TBs; (b) `hostile` entry-count bomb — millions of empty-file entries; both advertise dishonest `total`/`spool_bytes`. **Dest**: `dest_fresh` on a disk with bounded free space.
- **Steps**: apply each; watch host free space and process memory.
- **Correctness**: each aborts with a structured cap-exceeded error; the manager never pre-allocates on the daemon-claimed `total`.
- **Host-safety** (load-bearing): host free space never drops below a floor; `dest_fresh` does not fill the disk; process RSS bounded.
- **Incremental**: n/a.

#### HRD-06 — two concurrent exports of the same sandbox (adversarial M4)
- **Spec**: singleflight per root; `export_id`-keyed spools. **Fixture**: honest delta; two `export_changes` invocations launched together (two dests).
- **Steps**: run both concurrently; collect both results.
- **Correctness**: either the second fold is **rejected** with an already-in-flight error while the first completes, or the folds serialize and **both** succeed; in the success case each dest equals the merged view and neither reader observed the other's spool (no `export_id` mismatch, no truncated read).
- **Host-safety**: each dest is internally consistent; no cross-spool bytes; `.export/` holds at most the in-flight spools and is empty at teardown.
- **Incremental**: n/a.

#### HRD-07 — export under concurrent `checkpoint_squash` (B5, inv 3)
- **Spec**: B5, lease pinning. **Fixture**: a multi-layer delta at `@v13`; start `export_changes`, then fire `checkpoint_squash` mid-export.
- **Steps**: interleave; collect both results and the exported tree.
- **Correctness**: export completes on its snapshot; the exported tree equals the `@v13` merged delta (not the squashed shape); `checkpoint_squash` also succeeds; net stack is the squashed manifest.
- **Host-safety**: the export lease pinned its source layers (a leased layer dir was never deleted mid-read — `observability` shows the lease boundary); nothing outside dest.
- **Incremental**: n/a.

#### HRD-08 — export under a concurrent publish (inv 3)
- **Spec**: inv 3 (snapshot excludes later publishes). **Fixture**: delta at `@v_a`; start export; publish a new layer `@v_a+1` while the fold runs.
- **Steps**: interleave.
- **Correctness**: the exported delta and `manifest_version` are `@v_a`; the `@v_a+1` path is **absent** from the export; a subsequent export at `@v_a+1` includes it.
- **Host-safety**: nothing outside dest.
- **Incremental**: the second export skips the `@v_a` winners and writes only the new layer's paths.

#### HRD-09 — deep / large delta: converge or fail cleanly at the start-request ceiling (adversarial H5)
- **Spec**: H5 (30 s `REQUEST_READ_TIMEOUT_S` start-request bound; ~2 MiB/chunk loop). **Fixture**: (a) a 499-layer delta (squash-suite deep builder); (b) a ~1 GiB compressed delta. **Dest**: `dest_fresh`.
- **Steps**: `export_changes(dir)`; record wall-clock, chunk count, and the start-request duration.
- **Correctness**: **either** the export converges and the tree equals the merged view, **or** it fails with a clean, structured start-request-timeout / ceiling error (never a hang, partial-dest corruption, or orphaned lease). "Squash first" is verified as the mitigation: after `checkpoint_squash`, the same export converges.
- **Host-safety**: on the fail path, no partial dest is left in a corrupt state (dir converges on re-run; archive is temp+rename); `.export/` reaped.
- **Incremental**: wall-clock, chunk count, and start-request duration recorded (informational; no hard budget in v1).

#### HRD-10 — daemon restart mid-paging (adversarial M3 + H1)
- **Spec**: M3 (in-memory registry), H1 (export boot reap). **Fixture**: a delta large enough to span several chunks. **Dest**: `dest_fresh`.
- **Steps**: begin `export_changes`; kill + restart the daemon after the first chunk but before eof; observe the manager's next `read_export_chunk`; then re-run the export.
- **Correctness**: the interrupted invocation aborts with a structured **export-not-found** error (the in-memory `{export_id → spool}` registry dropped); the re-run rebuilds the spool and converges — the exported tree equals the merged view.
- **Host-safety** (load-bearing): the orphaned spool left under `<scratch_root>/.export/` is removed by the **export boot step** on restart (P4), not leaked; the partial `dest_fresh` converges on the re-run (dir idempotence).
- **Incremental**: the re-run is byte-identical to a clean export.

---

## 4. Traceability — spec invariant / axis → cases

| Spec item | Cases |
| --- | --- |
| inv 1 — read-only on layer-stack storage | teardown §1.3.2 (every case), HRD-07 |
| inv 2 — merged-delta equivalence + apply ordering | EZ-01, MED-03, **MED-04**, MED-07 |
| inv 3 — lease pins sources | HRD-07, HRD-08 |
| inv 4 — idempotent re-run (file winners) | MED-01, HRD-10 |
| inv 5 — published-only | EZ-09 |
| inv 6 — detach; manager sole host writer | P3, EZ-06/EZ-10 (no host write on reject) |
| inv 7 — archive atomicity | EZ-03, EZ-04, HRD-09 (fail path) |
| inv 8 — no fsync / re-run recovery | MED-01, HRD-10 |
| inv 9 — host-boundary safety | **HRD-01, HRD-02, HRD-03, HRD-04, HRD-05**, MED-06 |
| inv 10 — fidelity boundary | EZ-08, MED-09 (+ `.wh.` unambiguity via `route.rs:22-33`) |
| B1 primary / seed reproduction | EZ-01, MED-01 |
| B2 incremental | MED-02, MED-08 |
| B3 opaque masking | MED-03, MED-04 |
| B4 archive portability | EZ-03, EZ-04, MED-10 |
| B5 concurrent squash | HRD-07 |
| output contract (counts, live sessions) | EZ-02, EZ-07, EZ-09 |
| cost table (delta-cost, winners-only) | MED-05, MED-08 |
| adversarial C1 (tar-slip) | HRD-01, HRD-02, HRD-03 |
| adversarial C2 (opaque ordering) | MED-04 |
| adversarial H5 (timeout/scale) | HRD-09 |
| adversarial M3/M4 (registry / concurrency) | HRD-06, HRD-10 |
| adversarial H1 (boot reap) | P4, HRD-10 |

The `.wh.` whiteout-name ambiguity (adversarial H2) is closed **upstream** by
the reserved namespace (`stack/publish/route.rs:22-33`) and is covered by the
sibling `reserved_paths/` suite; export inherits the guarantee, so this
catalog asserts only marker **purity** in the dir applier (EZ-08), not
publishability.

## 5. Execution order & suite composition

1. **Preconditions** (§1.1 table) — once, hard-fail. P1 gates the whole
   catalog; P4 gates HRD-10.
2. **EZ-01…10** serial (`-m "export and easy"`) — the rebuild gate; EZ-01
   first (the primary workflow). Budget ≤ 4 min.
3. **MED-01…10** serial — one interaction each; MED-04 (ordering) is the
   must-not-regress case. Budget ≤ 8 min.
4. **HRD-01…08** serial — each rebuilds its own sandbox / hostile fixture.
   The host-boundary quartet (HRD-01…05) runs against the fault-injecting
   build; a failure here is **Critical** and stops the suite.
5. **HRD-09, HRD-10** last — scale and restart; own the wall-clock and
   boot-reap sentinels. Budget ≤ 15 min including deep-stack setup.
6. **Suite report** generated even on abort: `SUMMARY.md` plus every
   `verdict.json` under `test-reports/<RUN_ID>/` is the sign-off artifact.

**Red-first discipline.** Before the applier's canonicalization lands, run
HRD-01/02/03 against a naïve `dir.join(entry)` applier and record the escapes
(files created outside dest, a deleted `victim`) in the first `test-reports/`
bundle — those three must fail on **host-safety** pre-fix and pass after.
MED-04 is the analogous red-first case for the opaque-ordering (C2) fix: it
fails on **correctness** against a blind tar-order applier and passes only
with the per-directory three-pass apply.
