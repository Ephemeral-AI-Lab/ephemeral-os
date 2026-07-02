---
title: LayerStack Squash + Live Workspace Remount
tags:
  - ephemeral-os
  - layerstack
  - workspace
  - namespace
  - storage
status: implementation_plan
updated: 2026-07-02
---

# LayerStack Squash + Live Workspace Remount

Revised after the 2026-07-02 adversarial multi-agent review
(`adversarial_multi_agent_review_results.md`): commit-time GC replaces the
pinning-set delete, the staged switch gets a fresh workdir + strict unmount +
restore ladder, admission covers engine hooks, all durable remount state and
the quarantine mechanism are deleted, and boot collapses to reap-then-sweep.

## Goal

Bound LayerStack storage and lowerdir chain length **per invocation** by
squashing published layers into equivalent flattened layers, and migrate
**live** workspace sessions onto the compact chains so old layers reclaim
immediately instead of at session destroy.

Policy (inherits the verdict of the removed live-remount experiment,
`docs/layerstack-command-lease-live-remount_SPEC.md` @ `b6a1de0ac`, remapped
onto the current architecture where the shell runner lives in
`namespace-execution`):

```text
Squash squashes every squashable block; zero options, zero trigger policy.
Squash is singleflight per layerstack root: one guard spans plan → sweep.
Never mutate or delete a layer directory any live lease references.
Storage commit is the correctness boundary; it never depends on live remount.
At commit, re-read the latest manifest and replace only a still-contiguous
planned source run; reclaimed-vs-leased is decided by lease GC at that
instant, never by a plan-time snapshot.
Live remount is best-effort post-commit cleanup; pre-PONR skip/failure keeps
the old lease and never fails a committed squash.
Live remount only after kernel-gated mount proof, all-entrypoint admission,
all-task quiesce, and zero-pin proof.
Retarget lease metadata only as a record of a verified mount state.
Blocked session = healthy: keep its lease, next squash catches it.
Failed session past the point of no return without verified restore = faulty:
report it, then destroy it through the ordinary destroy path.
```

Environment facts (normative, load-bearing for recovery):

1. **Holders die with the daemon.** `spawn_ns_holder` sets
   `prctl(PR_SET_PDEATHSIG, SIGKILL)` in a `pre_exec` hook so this is
   kernel-enforced, not deployment luck (today only the pid-ns init carries a
   death signal, bound to the holder — `holder/namespace.rs:189`).
2. **No session survives a daemon restart.** `manager.json` identifies
   leftovers to clean at boot; it is never used to recover sessions.
   Uncommitted upperdir writes share the session's ephemeral fate by design.
3. **No remount state is ever persisted.** The remount transaction's only
   durable write is the existing active-handle rewrite after a verified
   switch. There is no quarantine mechanism.

Live remount is gated, not assumed. If any gate below is not proven in the
supported Docker sandbox environment, squash still commits compact layers but
leaves live sessions leased:

1. **Same-upperdir staged mount proof**: OLD and NEW overlays may coexist with
   the same upperdir — NEW always using a **fresh sibling workdir** — long
   enough for the staged `MS_MOVE` switch, with production-equivalent options
   (`userxattr`, no `index`) and exact lowerdir verification. The removed
   experiment's visible-options helper lacked `userxattr` and reused the live
   workdir; neither defect may be copied.
2. **All-task quiesce proof**: every task that can observe the holder mount
   namespace is stopped or allowlisted infrastructure. A command pgid is only
   a seed, never the whole proof.
3. **Startup cleanup proof**: leftover handles and run dirs are reaped before
   any boot storage sweep.

CLI surface (manager-owned operation, target sandbox argument):

```text
sandbox-cli manager checkpoint_squash --sandbox-id SANDBOX_ID
```

The manager command forwards one daemon-local runtime request,
`squash_layerstack`, to the selected ready sandbox. `squash_layerstack` is a
runtime dispatch operation, not a runtime CLI catalog entry; users call the
manager command.

Output contract (one JSON line on stdout; faults on stderr as `{"error":…}`):

```json
{
  "manifest_version": 14,
  "layers": ["L000012-…", "S000014-2a", "L000009-…", "…", "B000001-base"],
  "squashed_blocks": [
    { "squashed_layer_id": "S000014-2a",
      "replaced_layer_ids": ["L000011-…", "L000010-…"],
      "replaced_layers": "reclaimed" },
    { "squashed_layer_id": "S000014-2c",
      "replaced_layer_ids": ["L000002-…", "L000001-…", "L000000-…"],
      "replaced_layers": "leased",
      "leases": 1,
      "blocked_reasons": ["pinned:cwd_pinned_workspace"] }
  ]
}
```

`layers` = the current stack (a); `squashed_blocks` = this run's blocks vs
lease state (b). `replaced_layers` and `leases` are derived from what the
commit-time GC actually deleted and the post-sweep lease registry.
`blocked_reasons` lists distinct `class:detail` strings; only the class
prefix is contract-stable (`unsupported` | `quiesce_failed` | `pinned` |
`mount_uncertain` | `stage_failed`), the detail is free-form. No `no_op`, no
boundaries dump, no byte totals — byte accounting stays with the daemon
`layerstack` observability view.

## Vocabulary and invariants

| Name | Meaning |
| --- | --- |
| boundary | `Base` (never squashed), or a lease pin: the newest layer of some live lease's manifest, computed from the existing `LeaseRegistry::lease_newest_layers()` under the plan lock. A predicate, not a new type. |
| `SquashBlock` | Maximal contiguous run of ≥ 2 active-manifest layers with no boundary inside. |
| plan lease | An ordinary `acquire_snapshot` lease of the plan-time manifest. It pins every source for the lock-free build with zero new machinery; released by the commit's GC (success) or the error path. |
| `flatten` | Pure fold of a block's layer dirs into one changeset, newest-wins per path/subtree. Emits: an explicit entry for **every directory surviving in the block's merged view**; whiteouts/opaques only when they are the winner needed to mask lower layers, classified via `is_kernel_whiteout_meta` + the opaque marker and re-emitted through `write_kernel_whiteout` (both on-disk encodings accepted); regular-file winners as mode-preserving `WriteFile`, **hardlinked** from the immutable source when whole-file (`.bytes` counts logical bytes). Source walks are fd-relative and no-follow. |
| `SquashedLayer` | The one new layer per block; id prefix `S` (`S000014-2a`). |
| `LayerSubstitution` | Persisted ledger `<S-id>.sources.json` = `{ schema_version, sources }`, where `sources` is the fully L-expanded original run (inner S ledgers expanded at build time). A missing, unknown-version, or degenerate (`|sources| < 2`) ledger fails that substitution closed — skipped, worst case identity. |
| storage commit | Storage-only transaction that builds/promotes `S` layers and atomically replaces `manifest.json`; live remount is not part of this correctness boundary. |
| commit recheck | Inside the commit's exclusive critical section, re-read the **latest** manifest. If the planned source run is still present and contiguous, replace it and commit `version = latest + 1`; otherwise abort `manifest_conflict`. Under normative singleflight the conflict is defensive — publishes only prepend. |
| post-commit remount sweep | Best-effort cleanup after storage commit, inside the same singleflight guard, that tries to move live sessions to compact chains and release old leases. It cannot undo or fail a committed squash. |
| clean remount skip | Any remount abort before the first `MS_MOVE` **returns success**: release replacement lease if any, keep old lease/session, resume tasks, report `leased` with `class:detail`. |
| new publish tail | Layers published after squash planning starts but before commit; they stay after the squashed layer and are compacted by a later run. |
| live-session retained space | Old layer bytes kept only because live sessions still use old lease chains; removed by successful remount or session exit/destroy. |
| faulty remount | Failure at/after the first successful `MS_MOVE` that the restore ladder cannot verifiably undo: report it (session id, phase, upperdir bytes, lease errors), then destroy the session through the ordinary destroy path. Namespace death is the unmount proof that lets both leases release. |
| rewritten manifest | Expand-then-contract, single pass: expand the lease manifest to L-form using the ledgers of its **own** S layers (alive because leased — sidecars die with the layer dir), then contract every contiguous run matching an **active-manifest** S ledger's `sources` (alive via the manifest). Same logical snapshot, shorter chain; terminates by construction. |
| `acquire_rewritten_lease` | One call under one **shared** writer-lock guard: expand-then-contract, validate rewritten layers alive, acquire the replacement lease — or return `Identity`. Never releases the old lease. |
| old-lease release | The existing `release_lease` under the **exclusive** writer lock, called only after visible mount verification, active-handle persistence, and task resume; refcount GC deletes what nothing references. Not a new lease API. |
| quiesce | Discover every task that can observe the holder mount namespace — union of session cgroup members, a full `/proc` scan for `ns/mnt == holder`, and the infrastructure allowlist (ns-holder, pid-ns init, remount runner: exempt from freeze, still pin-inspected). SIGSTOP the rest, poll `/proc/*/stat` to `T` within the freeze budget, verify membership stable. Any discovered task in a different mount namespace blocks (`pinned:mount_namespace_escaped`). The command pgid is only a discovery seed. |
| pin | A frozen task's `cwd`/`root`/open fd/mapped file/child mount inside the workspace mount, an un-allowlisted `anon_inode` fd (io_uring, fanotify, …), a tracer outside the frozen set, or **any** inspection read error. Any pin ⇒ no live switch. |
| staged switch | Kernel-gated mount of the NEW overlay at staging — same upperdir, fresh sibling workdir — probe, MS_MOVE old→rollback, MS_MOVE staging→root, re-mask, probe, strict-unmount rollback (restore ladder on EBUSY). |
| strict unmount | A single `umount2(path, 0)` with no lazy/`MNT_DETACH` fallback. Only strict unmount or namespace death counts as "unmounted". |
| restore ladder | On post-PONR failure: MS_MOVE new→staging, MS_MOVE rollback→root, re-probe the OLD mount, unmount staging (zero users — tasks stayed frozen). Verified restore ⇒ `leased(pinned:rollback_unmount_busy)`; unverified ⇒ faulty. |
| point of no return | The first `MS_MOVE` **returning success**. Failures before it — including a failed first `MS_MOVE` — leave the session untouched (clean skip). At/after it: verified restore or faulty. The runner always returns a phase-tagged report (`first_move_succeeded`, present on error paths too); a missing or ambiguous report at/past that phase is treated as post-PONR. |

Invariants:

1. **Merged-view equivalence** — for every path, the merged view of the
   rewritten/post-squash manifest equals the pre-squash view, explicitly
   including directory-only shapes (a directory created then emptied survives
   flatten). Squash changes structure, never content; remount is invisible to
   session semantics.
2. **Pin-overlap** — the replacement lease is acquired before the old one is
   released; no instant exists where either chain is unpinned. Clean aborts
   release only the replacement lease.
3. **Never-straddle** — lease manifests are contiguous history prefixes and
   blocks never cross plan-time pins, so every substitution is fully-inside or
   fully-disjoint for every lease, any generation. Every session is always
   rewritable (possibly to identity).
4. **Detach-before-delete** — old lowerdirs are deleted only via lease
   release after the old mount is strictly unmounted or its namespace died.
   Lazy detach never counts as proof.
5. **Upperdir sanctity** — remount never touches the upperdir. The workdir is
   per-mount kernel-transient state: the staged NEW mount always gets a fresh
   sibling workdir (`work-remount-<n>`), and the old workdir is deleted with
   old-lease cleanup. The same-upperdir kernel gate proves OLD and NEW coexist
   with production-equivalent options (`userxattr`, no `index`); without that
   proof, live remount is disabled.
6. **Ordering contract**:
   `build → stage → verify staged → switch → verify visible → strict-unmount
   rollback → persist active handle → resume → release old lease/refcount GC`.
   Never retarget first; never release the old lease before tasks can run
   again; never release the old lease without the new handle durably persisted
   (persist failure ⇒ resume on the new mount holding **both** leases; the old
   run reclaims at session destroy).
7. **Session lifecycle is not squash's business** — remount never
   creates/captures/publishes. The single exception: a session past the point
   of no return without verified restore is reported as faulty and then
   destroyed through the ordinary session-destroy path.
8. **Commit/cleanup separation** — phases 1–3 may fail the storage commit;
   phase 4 only changes garbage-collection state and live-session retained
   space. A remount skip/failure before the point of no return never rolls
   back, retries, or fails a committed storage commit.

Lock discipline: sessions-map mutex < per-session admission gate < storage
writer lock. Never wait on the admission gate while holding the sessions-map
mutex; never hold the storage writer lock across quiesce or the staged switch
(`acquire_rewritten_lease` is shared; only release/GC and the commit are
exclusive).

---

## A. Expected file/folder structure with LoC change

`(new ~N)` = new file with estimated LoC; `(+N)` = lines added to existing
file. Calibrated against existing module sizes (`publish/plan.rs` 241,
`publish/model.rs` 147, service impls 26–64).

```text
crates/sandbox-runtime/layerstack/
├── src/stack/squash.rs                      (new ~280)  plan → build → commit; block model;
│                                                        reclaim classification from GC result
├── src/stack/squash/flatten.rs              (new ~180)  layer-dir walk → winning changeset (pure):
│                                                        dir entries, whiteout re-emission, hardlink
├── src/stack/squash/rewrite.rs              (new  ~70)  ledger load + expand-then-contract (pure)
├── src/stack/sweep.rs                       (new  ~80)  boot storage sweep behind writer lock
│                                                        (fail-closed; keep-set = active manifest)
├── src/stack/ops/publish.rs                 (+40)       extract shared commit tail (promote →
│                                                        sidecars → recheck → manifest write) used
│                                                        by publish and squash
├── src/storage/fs.rs                        (+30)       sources sidecar helpers; fsync_tree extended
│                                                        to every directory, bottom-up
├── src/stack/lease/cleanup.rs               (+12)       remove digest/bytes/sources sidecars with
│                                                        layer dir (fixes leaked .bytes); set-based
│                                                        membership checks
├── src/{lib,stack/mod,stack/lease/mod,storage/mod}.rs   (+25)  wiring + exports
└── tests/unit/{squash.rs (new ~380), sweep.rs (new ~60)} · tests/unit.rs (+2)

crates/sandbox-runtime/overlay/
├── src/kernel_mount.rs                      (+70)       real-path lowerdir mode on the production
│                                                        builder (mountinfo lowerdir= proof, option
│                                                        parity by construction), move_mountpoint,
│                                                        strict_unmount (no lazy fallback)
├── src/lib.rs                               (+3)        exports
└── tests/unit/kernel_mount.rs               (+60)

crates/sandbox-runtime/namespace-execution/
├── src/quiesce.rs                           (new ~240)  all-task holder-scope quiesce: cgroup ∪
│                                                        /proc ns-scan ∪ allowlist discovery,
│                                                        SIGSTOP, poll-stopped ≤ freeze budget,
│                                                        /proc pin inspection, resume-on-drop guard
├── src/engine.rs                            (+30)       engine.remount_overlay beside mount_overlay
├── src/lib.rs                               (+3)
└── tests/{quiesce.rs (new ~80), engine.rs (+25)}

crates/sandbox-runtime/namespace-process/
├── src/runner/setns/remount_overlay.rs      (new ~260)  staged switch: RemountMaskGuard,
│                                                        staging+rollback mountpoints, probes,
│                                                        MS_MOVE pair, strict rollback-unmount,
│                                                        restore ladder, phase-tagged report
│                                                        (port of removed runner/setns.rs body with
│                                                        two mandatory deltas: strict unmount and
│                                                        userxattr/option parity)
├── src/runner/{setns,protocol,mod}.rs       (+30)       op entry, request fields incl. fresh
│                                                        workdir path, dispatch
└── tests/unit/runner/setns.rs               (+60)

crates/sandbox-runtime/workspace/
├── src/lifecycle/remount.rs                 (new ~150)  the whole remount transaction: rewritten
│                                                        lease → freeze → runner → verify → persist
│                                                        active handle → resume → release old lease;
│                                                        failure rules of C5
├── src/service/impls/remount_workspace.rs   (new  ~40)  thin impl delegating to lifecycle
├── src/lifecycle/recover.rs                 (new  ~70)  boot reap: destroy leftover handles/run
│                                                        dirs before the storage sweep
├── src/namespace/setns_runner.rs            (+35)       NamespaceRuntime::remount_overlay
├── src/namespace/holder.rs                  (+5)        pre_exec PR_SET_PDEATHSIG(SIGKILL) —
│                                                        holders provably die with the daemon
├── src/session/state.rs                     (+6)        MountedWorkspace gains the active workdir
│                                                        path; NO remount state enum
├── src/lifecycle/persistence.rs             (+4)        persist active workdir with the handle
├── src/{model,service,service/impls/mod,lib}.rs         (+30)
└── tests/unit/{remount.rs (new ~170), recover.rs (new ~60)} · tests/unit.rs (+2)

crates/sandbox-runtime/operation/
├── src/layerstack/service/impls/squash.rs   (new ~110)  daemon-local squash_layerstack op: storage
│                                                        squash + per-session sweep loop + result
│                                                        assembly (sweep lives here, not services.rs)
├── src/workspace_session/service/impls/remount_session.rs (new ~60)  per-session gate hold,
│                                                        snapshot, delegate, registry refresh
├── src/workspace_session/…                  (+25)       route run_file_op/capture/destroy through
│                                                        the per-session admission gate
├── src/command/service/core.rs              (+15)       exec launch AND finalize/timeout completion
│                                                        hooks through the same gate
├── src/operation.rs                         (+8)        OperationEntry::internal for non-CLI ops
├── src/services.rs                          (+10)       boot hook: recover + storage sweep, once,
│                                                        before serving
├── src/layerstack/service/{model,mod}.rs, src/workspace_session/…/mod.rs   (+35)  DTOs, exports
└── tests/layerstack_squash.rs (new ~300) · tests/support/mod.rs (+25)

crates/sandbox-manager/
├── src/operation/management/service/impls/checkpoint_squash.rs (new ~40)
│                                                        parse sandbox id, forward squash_layerstack
│                                                        (modeled on destroy_sandbox; reuses
│                                                        endpoint/client helpers)
├── src/operation/cli_definition/management_operations.rs (+30)  checkpoint_squash CliOperationSpec
│                                                        (family "checkpoint" is a string label,
│                                                        not a directory tree)
├── src/operation/{mod,dispatch,specs}.rs    (+10)       register
└── tests/manager_core.rs                    (+50)       catalog + forwarding tests

crates/sandbox-observability/
└── src/record.rs                            (+8)        LAYERSTACK_SQUASH, WORKSPACE_SESSION_REMOUNT,
                                                         NAMESPACE_EXEC_REMOUNT_OVERLAY

sandbox-protocol / sandbox-daemon / sandbox-gateway   (+0)
```

Totals: **12 new source files ≈ 1,580 LoC**, **≈ +465 LoC** in existing files,
**≈ 1,270 LoC** of tests → ≈ 3,300 LoC end to end.

Build order: layerstack pure parts (flatten/rewrite) → shared commit tail
extraction → squash transaction + boot sweep → overlay real-path/strict
helpers → namespace-execution quiesce → namespace-process staged runner →
workspace transaction + boot recover + PDEATHSIG → operation admission gate +
op impl + sweep loop → manager CLI spec.

## Storage layout and transaction

Squash creates exactly **one temp dir per block** under `staging/`, nonce-named
via the existing `allocate_layer_dirs` path; remount creates **zero layers**
(its only disk write is the atomic `manager.json` active-handle rewrite).
Storage commit ends at the durable manifest rename; remount, old-lease
release, and GC are cleanup.

```text
<layer_stack_root>/
├── manifest.json                       the ONLY commit point (write_atomic: tmp → rename → dir fsync)
├── workspace.json                      binding — never touched
├── .storage-writer.lock                flock: one owning process per root
├── layers/
│   ├── B000001-base/                   never touched, never swept (B-prefix protected)
│   ├── L… (pins, singletons)           never touched
│   ├── L… (block sources)              deleted only by lease GC, never before
│   └── S000014-2a/                     ← promoted from staging by same-fs rename(2)
├── staging/
│   └── S000014-2a-<nonce>.staging/     ← the temp layer: flatten output (hardlinked/copied
│                                         winners, whiteouts, opaque markers, dir entries)
└── .layer-metadata/
    ├── S000014-2a.digest               existing digest sidecar
    ├── S000014-2a.bytes                existing byte-count sidecar (logical bytes)
    └── S000014-2a.sources.json         substitution ledger (remount input)
```

| # | Phase | Lock | Disk mutation | Crash here | Cleaner |
| --- | --- | --- | --- | --- | --- |
| 1 | plan + pin | shared, brief | none | — | plan lease drops with its guard |
| 2 | build | none (plan lease pins sources) | staging trees fsynced **bottom-up including every directory and whiteout** | orphan staging | error path; boot sweep |
| 3 | **commit** — one exclusive critical section: recheck → promote → sidecars → manifest rename → release plan lease (refcount GC) | exclusive | rename to `layers/S…`; fsync `layers/`; write digest/bytes/sources via `write_atomic` (fsyncs file + parent — no extra dir fsync); atomic `manifest.json` replace + parent fsync; GC deletes sources no lease references | before rename: old manifest valid — in-process error path removes the promoted S dir + sidecars (mirrors publish); after rename: committed | in-process error path; boot sweep |
| 4 | post-commit remount sweep | shared per session (`acquire_rewritten_lease`); exclusive per migrated session (old-lease release + GC) | one `manager.json` active-handle rewrite per **migrated** session — the sweep's only durable write; blocked sessions write nothing | committed squash remains valid; sessions die with the daemon | boot cleanup |

Only phases 1–3 are the storage commit path. Phase 4 improves reclaim latency
when it succeeds, but cannot change the result of a committed squash.
`replaced_layers: reclaimed|leased` and `leases` are derived from what the
phase-3 GC actually deleted plus the post-sweep lease registry — never from
plan-time pinning snapshots (a lease acquired between plan and commit under
the shared lock legitimately pins the run; GC sees it, a snapshot would not).

Squash is **singleflight per layerstack root**: one in-process guard spans
plan → sweep; the `.storage-writer.lock` flock already excludes other
processes. Peak temporary storage is therefore one builder's staging.

Boot cleanup (once at daemon start, before serving):

1. **Fail closed.** Destructive sweep requires a successfully parsed
   `manifest.json` with `version ≥ 1` and a non-empty layer list. `B*` ids are
   never deleted; deletion never crosses a mount boundary.
2. **Reap.** Holders cannot outlive the daemon (PDEATHSIG), so every persisted
   `manager.json` handle is a dead session: destroy its run dir, drop the
   handle, emit the observability record. No lease recreation, no
   orphan-liveness proof, no task resume, no per-record branching.
3. **Sweep.** Delete `staging/*`, `layers/*`, and metadata sidecars not
   referenced by the active manifest.

Metadata cleanup deletes the existing `.digest` and `.bytes` sidecars plus the
new `.sources.json` sidecar for the same layer id (also fixing today's leaked
`.bytes` sidecar).

---

## B. Squash workflows

Legend: `Ln` published layer, `B` base, `S` squashed layer, `◀ pin` = newest
layer of a live lease's manifest, blocks are maximal runs ≥ 2 between
boundaries.

### B1. Simple — idle stack, no leases

```text
active v4                     blocks              active v5        reclaim
┌────┐
│ L3 │ ─┐                                         ┌────┐
│ L2 │  ├── one block ──▶  S1 = flatten(L3,L2,L1) │ S1 │           L3,L2,L1 deleted at
│ L1 │ ─┘                                         ├────┤           commit by the phase-3
├────┤                                            │ B  │           GC (no live lease
│ B  │                                            └────┘           references them)
└────┘
report: S1 → replaced_layers: "reclaimed"
```

No sessions ⇒ no sweep, no deferral, disk drops in one invocation.

### B2. Medium — one session pinning the whole chain

```text
active v6                blocks                active v7            sweep
┌────┐
│ L5 │ ◀ pin ws-1        kept                  ┌────┐    ws-1 idle (no live exec):
│ L4 │ ─┐                                      │ L5 │    plain staged switch,
│ L3 │  ├─ block ─▶ S1   pinned by ws-1        │ S1 │    lease [L5,L4..L1,B] →
│ L2 │  │                                      ├────┤          [L5,S1,B]
│ L1 │ ─┘                                      │ B  │    old L4..L1 deleted on
├────┤                                         └────┘    old-lease release
│ B  │
└────┘
report: S1 → "reclaimed"   (deleted_after_migration)
```

The pin layer `L5` is kept verbatim (never copied); only the run below it
flattens. ws-1's view is bitwise unchanged; its chain drops 5 → 3 lowerdirs.

### B3. Complex — multiple pins, singleton runs, one blocked session

Sessions: ws-A leased @v9, ws-B and ws-D @v5, ws-C @v3. ws-D has an
interactive shell running (cwd-pinned).

```text
active v13 (newest→base)    boundary          block/pinning              active v14
┌─────┐
│ L12 │                      —                 ┌ singleton? no: ┐      ┌─────┐
│ L11 │ ─┐                                     │ no live lease  │      │ L12 │
│ L10 │ ─┴─────────────────────────▶ S1        │ references run │      │ S1  │
├─────┤                                        └────────────────┘      ├─────┤
│ L9  │ ◀ pin ws-A          LeasePin           kept                    │ L9  │
│ L8  │ ─┐                                                             ├─────┤
│ L7  │  ├─────────────────────────▶ S2        { ws-A }                │ S2  │
│ L6  │ ─┘                                                             ├─────┤
├─────┤                                                                │ L5  │
│ L5  │ ◀ pin ws-B, ws-D    LeasePin           kept                    │ L4  │
│ L4  │                      singleton run     kept (1 layer < 2)      │ L3  │
├─────┤                                                                ├─────┤
│ L3  │ ◀ pin ws-C          LeasePin           kept                    │ S3  │
│ L2  │ ─┐                                                             ├─────┤
│ L1  │  ├─────────────────────────▶ S3        { ws-A,B,C,D }          │ B   │
│ L0  │ ─┘                                                             └─────┘
├─────┤                                                                14 → 9 layers
│ B   │
└─────┘

ledger:  S1.sources=[L11,L10]  S2.sources=[L8,L7,L6]  S3.sources=[L2,L1,L0]
commit:  L11,L10 deleted by the phase-3 GC (no live lease references them)

sweep:
  ws-A [L9 L8 L7 L6 L5 L4 L3 L2 L1 L0 B] ── S2 ✓, S3 ✓ ──▶ [L9 S2 L5 L4 L3 S3 B]   migrated
  ws-B [L5 L4 L3 L2 L1 L0 B]             ── S3 ✓        ──▶ [L5 L4 L3 S3 B]         migrated
  ws-C [L3 L2 L1 L0 B]                   ── S3 ✓        ──▶ [L3 S3 B]               migrated
  ws-D shell frozen → pinned:cwd_pinned_workspace → SIGCONT, lease untouched        leased

reclaim cascade:      L11 L10 │ L8 L7 L6 │ L2 L1 L0
  commit               DELETED│ pinned:A │ pinned:A,B,C,D
  ws-A migrates               │ DELETED  │ pinned:B,C,D
  ws-B migrates               │          │ pinned:C,D
  ws-C migrates               │          │ pinned:D   ← report: S3 "leased", 1 lease
  ws-D shell exits / next squash / destroy            → DELETED
```

Every substitution is fully-inside or fully-disjoint per lease
(never-straddle), so each rewrite either applies whole or skips whole.

### B4. Ultra-complex — two generations, generation-crossing rewrite, races, one faulty session

```text
gen-1  active v9: [L8 L7 L6 L5 L4 L3 L2 L1 B]
       ws-1 @v8 (interactive shell, cwd-pinned)  ws-2 @v4 (idle)
       pins: L8(ws-1), L4(ws-2)  →  blocks [L7 L6 L5]→Sa, [L3 L2 L1]→Sb
       commit v10: [L8 Sa L4 Sb B]
       sweep: ws-2 → [L4 Sb B] (Sb applied); L3..L1 still pinned by ws-1 → keep
              ws-1 → pinned:cwd_pinned_workspace → leased (everything it pins stays)

t1     ws-1 shell exits; user destroys ws-1 → its release frees L7 L6 L5 L3 L2 L1
       publishes land: v12 = [L11 L10 L8 Sa L4 Sb B]
       ws-3 created @v10-era manifest (pins L10…); runs a CLEAN batch cmd
       (network wait, no cwd/fd/mmap/child mount under workspace)

gen-2  pins: L10(ws-3), L4(ws-2)
       blocks: [L11] singleton→kept · [L8 Sa]→Sc (RE-SQUASH of a prior S) ·
               [Sb] singleton→kept
       mid-build race: publish L12 lands → commit recheck still passes
       (publishes only prepend); commit v13: [L12 L11 L10 Sc L4 Sb B]
       ledger now:
         Sa.sources=[L7,L6,L5]
         Sb.sources=[L3,L2,L1]
         Sc.sources=[L8,L7,L6,L5]        (inner Sa expanded at build time)

       sweep:
         ws-3 live protocol: freeze → ZERO pins → staged switch → verify →
              rewrite: expand [L10 L8 Sa L4 Sb B] → [L10 L8 L7 L6 L5 L4 L3 L2 L1 B],
              contract Sc ✓, Sb ✓ → [L10 Sc L4 Sb B]
              → persist active handle → SIGCONT → release old lease → L8, Sa deleted
                                                                    migrated (live)
         ws-2 rewrite: expand [L4 Sb B] → contract → [L4 Sb B] = identity → untouched
         ws-4 (hypothetical): clean pins, but rollback-unmount returns EBUSY
              AND the restore ladder's re-probe of the OLD mount fails →
              past the point of no return without verified restore → FAULTY:
              reported (id, phase, upperdir bytes), then destroyed through the
              ordinary destroy path → namespace death → lease release reclaims
              everything only it pinned                              faulty

crash  daemon dies between promote and manifest write of some gen:
       boot keeps the old manifest, reaps every leftover session run dir
       (holders died with the daemon), and sweeps the orphan layers/S… +
       sidecars. A crash mid-remount needs no special record: the session is
       dead either way; the sweep keeps exactly the active manifest's layers.
```

Expansion uses only the lease's own S ledgers (alive because leased — the
sidecar dies with the layer dir), contraction only active-manifest S ledgers
(alive via the manifest), so a lease created in gen-0 crosses gen-1 and gen-2
substitutions in one pass. A missing/unknown/degenerate ledger fails that
substitution closed — worst case identity, never a wrong chain.

### B5. Ultra-complex, non-faulty — live protocol under stress, clean aborts only

Same world as B4 generation 2 (post-commit v13 = `[L12 L11 L10 Sc L4 Sb B]`,
`Sc.sources = [L8,L7,L6,L5]`), but the sweep hits every hard path and still
ends with **zero faulty sessions** — "faulty" is a narrow, provable condition,
not a synonym for "something went wrong".

```text
sweep over four sessions:

ws-3  batch cmd waiting on a socket, no workspace pins        FULL LIVE PROTOCOL
      freeze: all-task stop → /proc poll → all 'T' in ~40 ms
      inspect: cwd not under workspace ✓ · root / ✓ · fds {socket:[…], /dev/null} ✓ ·
               maps {libc,…} ✓ · mountinfo shows /workspace, ns/mnt == holder ✓ → ZERO pins
      rewrite: [L10 L8 Sa L4 Sb B] → [L10 Sc L4 Sb B]
      stage NEW (fresh workdir) → probe ✓ → MS_MOVE pair → masks ✓ → probe ✓
      strict umount rollback ✓  ← the PROOF STEP PASSES: nobody held the old mount
      persist active handle → SIGCONT → release old lease → L8, Sa deleted
      the command never notices: a socket fd is not a workspace object,
      absolute lookups re-resolve onto the new mount, and upperdir writes
      made before the freeze are still visible after         → migrated (live)

ws-2  rewrite is identity (expand-then-contract returns the same chain)
      short-circuits before any freeze                       → untouched

ws-5  interactive PTY bash at prompt, cwd=/workspace/src
      freeze ✓ → inspect: cwd pin → SIGCONT within ~50 ms    → leased
      (pinned:cwd_pinned_workspace; the shell never observes anything;
       deliberately NO registry short-circuit — every session takes the same
       freeze → inspect path, and the stall is paid only on explicit
       checkpoint_squash invocations)

ws-6  clean batch cmd, BUT the staging mount fails ENOSPC
      (the transient commit peak B+P+F filled the disk)
      failure is BEFORE the point of no return — the old
      mount was never moved; its workdir was never reused
      → SIGCONT → session untouched                          → leased
                                                   (stage_failed:staging_mount_enospc)

next invocation, minutes later (ws-3's migration freed space; ws-5's shell
has exited):
      ws-5 → plain switch → migrated · ws-6 → live protocol → migrated
      their old runs delete; the stack converges with no retry machinery,
      no persisted sweep state, zero faulty outcomes across both runs
```

The line between B5 and B4 is one proof: B5's strict `umount(rollback)`
succeeded; B4's ws-4 got EBUSY **and** the restore ladder could not verify the
old mount back. Only that narrow path is faulty. Everything before the first
successful `MS_MOVE` aborts clean and reports `leased`; a verified restore
after it also reports `leased(pinned:rollback_unmount_busy)`.

## CLI `checkpoint_squash` output examples

`sandbox-cli` prints the result value as **one compact JSON line on stdout**
(exit 0); faults are one `{"error":…}` line on **stderr** (exit 1).
Pretty-printed here for readability.

**B1 — simple, everything reclaimed:**

```json
{
  "manifest_version": 5,
  "layers": ["S000005-0000001a", "B000001-base"],
  "squashed_blocks": [
    { "squashed_layer_id": "S000005-0000001a",
      "replaced_layer_ids": ["L000003-…", "L000002-…", "L000001-…"],
      "replaced_layers": "reclaimed" }
  ]
}
```

**Nothing to squash** (no `no_op` flag — the state speaks for itself):

```json
{
  "manifest_version": 5,
  "layers": ["S000005-0000001a", "B000001-base"],
  "squashed_blocks": []
}
```

**B3 — complex, mixed reclaim and one blocked shell:**

```json
{
  "manifest_version": 14,
  "layers": ["L000012-…", "S000014-2a", "L000009-…", "S000014-2b",
             "L000005-…", "L000004-…", "L000003-…", "S000014-2c", "B000001-base"],
  "squashed_blocks": [
    { "squashed_layer_id": "S000014-2a",
      "replaced_layer_ids": ["L000011-…", "L000010-…"],
      "replaced_layers": "reclaimed" },
    { "squashed_layer_id": "S000014-2b",
      "replaced_layer_ids": ["L000008-…", "L000007-…", "L000006-…"],
      "replaced_layers": "reclaimed" },
    { "squashed_layer_id": "S000014-2c",
      "replaced_layer_ids": ["L000002-…", "L000001-…", "L000000-…"],
      "replaced_layers": "leased",
      "leases": 1,
      "blocked_reasons": ["pinned:cwd_pinned_workspace"] }
  ]
}
```

**B5 — multiple leases, distinct reasons on one block** (ws-3 migrated so it
no longer pins; ws-5 and ws-6 still do):

```json
{
  "manifest_version": 13,
  "layers": ["L000012-…", "L000011-…", "L000010-…", "S000013-3c",
             "L000004-…", "S000010-2b", "B000001-base"],
  "squashed_blocks": [
    { "squashed_layer_id": "S000013-3c",
      "replaced_layer_ids": ["L000008-…", "S000010-2a"],
      "replaced_layers": "leased",
      "leases": 2,
      "blocked_reasons": ["pinned:cwd_pinned_workspace",
                          "stage_failed:staging_mount_enospc"] }
  ]
}
```

**Storage commit faults — one JSON line on stderr, exit 1.** The commit
recheck is run-presence, not version equality — publishes only prepend, so a
racing publish never conflicts and squash cannot starve. The conflict is
defensive (singleflight excludes competing squashes; safe to invoke again):

```json
{"error":{"kind":"manifest_conflict","message":"planned source run no longer contiguous: planned at version 13, found version 15","details":{"planned_version":13,"found_version":15}}}
```

Anything else before storage commit (build I/O failure, flatten failure,
promote/metadata failure):

```json
{"error":{"kind":"operation_failed","message":"layer-stack storage error: …","details":{}}}
```

In fault cases the manifest is untouched unless the phase-3 manifest rename
was reached; partial progress before it is never referenced state, and a
post-promote in-process failure removes the promoted S dir and its sidecars
before returning. After commit, reclaim failures leave old runs for boot
sweep or the next cleanup pass; they do not invalidate the manifest.

Post-commit remount errors are not storage commit faults. Before the first
successful `MS_MOVE`, report `leased(class:detail)` and keep the old lease.
At/after it, a verified restore reports `leased(pinned:rollback_unmount_busy)`;
otherwise report `faulty` and destroy.

**With `--progress`** — after manager-to-daemon progress forwarding is wired,
build/sweep telemetry streams on stderr; the result stays one line on stdout.
A faulty-session outcome must appear in this agreed result/progress surface
with session id, phase, upperdir bytes discarded, and lease-release errors; it
must not be observability-only. There is no quarantine: faulty sessions are
destroyed through the ordinary destroy path after reporting:

```text
$ sandbox-cli --progress manager checkpoint_squash --sandbox-id sb-1
[progress 0.102s] squash plan: 1 block, 2 source layers, 2 pins
[progress 1.211s] flatten S000013-3c: entries=1204 hardlinked=1100 copied=104 bytes=18320411
[progress 1.540s] commit: manifest v12 -> v13
[progress 1.902s] remount ws-3: frozen 3 procs, 0 pins, staged switch verified
[progress 2.480s] remount ws-5: blocked pinned:cwd_pinned_workspace, resumed
[progress 2.815s] remount ws-4: rollback unmount EBUSY, restore unverified -> faulty phase=rollback_unmount upperdir_bytes=1832 action=destroy
[progress 3.407s] remount sweep: 1 migrated, 2 leased, 1 faulty
[Output]
{"manifest_version":13,"layers":[...],"squashed_blocks":[...]}
```

---

## C. Remount workflow — workspace + namespace shell runner

### C1. Sweep decision tree (per session, inside the squash operation)

The admission gate is **one per-session gate owned by the workspace-session
service**. It blocks exec launch, one-shot create/finalize **including the
engine completion/timeout/cancel hooks** (`finalize_one_shot` must acquire it
— the PTY deadline SIGKILL itself is benign, task death only sheds pins; the
hook's capture/destroy is what must wait), file read/write/edit, capture,
destroy, namespace runner entrypoints, and remount. Command core routes
through it; there is no separate lifecycle lock for this feature and no
version token — the gate is held across the whole per-session attempt.
Session-not-found at the gate is a silent skip.

```text
                acquire per-session admission gate
                (session gone → silent skip, release nothing)
                                    │
        ┌───────────────────────────┼──────────────────────────────┐
   no observable tasks        all-task freeze+inspect          freeze/inspect finds pins,
   (discovered set ⊆          proves ZERO pins                 escape, or uncertainty
    infrastructure allowlist)      │                                │
        ▼                          ▼                                ▼
  plain staged switch         full live protocol               SIGCONT immediately,
  (same gate; no freeze)      (C2/C3 below)                    release replacement lease,
        │                          │                           lease untouched
        ▼                          ▼                                ▼
    migrated                   migrated                     "leased" + class:detail
                                                            (caught by NEXT squash run —
                                                             no retry machinery exists)
   past the point of no return, any path:
     restore ladder verified  ──▶ leased(pinned:rollback_unmount_busy)
     restore unverified/none  ──▶ faulty report + destroy (ordinary destroy path)
```

Every session takes the same freeze → inspect path — there is deliberately no
registry-based short-circuit for predictably pinned sessions; the ~50 ms stall
is paid only on explicit `checkpoint_squash` invocations, and one uniform
evidence-based pipeline beats a second classification path.

### C2. Full sequence — crate swimlanes

```text
operation squash op      workspace-session       namespace-execution   workspace crate      namespace-process runner       layerstack
──────────────────────   ─────────────────────   ───────────────────   ──────────────────   ────────────────────────────   ─────────────────
squash committed (phases 1–3)
for each session in the post-commit snapshot:
  admission ────────────▶ per-session gate
                          (session gone → skip)
  acquire_rewritten_lease ─────────────────────────────────────────────────────────────────────────────────────▶ expand-then-contract +
                                                                                                                  validate + acquire
                                                                                                                  (one SHARED-lock guard)
  Identity → unlock gate, next session
  discover + freeze ─────────────────────▶ quiesce.rs: cgroup ∪ ns-scan ∪
                                           allowlist; SIGSTOP; poll 'T' ≤
                                           freeze budget; membership stable
  inspect ───────────────────────────────▶ /proc pins + child mounts (C4)
  (pins/escape/uncertainty → release replacement lease, SIGCONT,
   unlock, "leased" + class:detail)
  staged switch ──────────────────────────────────────────────────────────▶ setns user+mnt →
                                                                            staged switch (C3) →
                                                                            phase-tagged report (ALWAYS)
  report ok → swap MountedWorkspace.snapshot (+ fresh workdir path),
              persist active handle          ← the sweep's ONLY durable write
  persist fails → SIGCONT, keep BOTH leases in memory (released at destroy),
                  "leased" mount_uncertain:active_persist_failed
  SIGCONT ───────────────────────────────▶ resume guard
  release old lease ───────────────────────────────────────────────────────────────────────────────────────────▶ release_lease (EXCLUSIVE)
                                                                                                                  → refcount GC
  unlock gate
  report pre-PONR abort            → release replacement lease, SIGCONT, unlock, "leased" + class:detail
  report post-PONR, restore OK     → release replacement lease, SIGCONT, unlock, "leased" pinned:rollback_unmount_busy
  report post-PONR, no restore /
  report missing or ambiguous      → report faulty (id, phase, upperdir bytes, lease errors),
                                     destroy session via the ordinary destroy path;
                                     namespace death → both leases release → refcount GC
```

### C3. The staged switch (inside the holder's mount namespace)

Preconditions:

- same-upperdir/fresh-workdir kernel gate passed in live Docker;
- the staged NEW mount is built by the **production `kernel_mount` builder in
  real-path mode** (validated real lowerdir paths instead of `/proc/self/fd/N`)
  so mountinfo exposes the exact `lowerdir=` list while `userxattr`/no-`index`
  parity holds **by construction** — the removed visible-options helper lacked
  `userxattr` (deleted files would resurface) and must not be copied verbatim;
- all-task quiesce proof holds, or the discovered set is only allowlisted
  infrastructure.

```text
/workspace ── overlay OLD [l4, n3, n2, n1] (hidden-path masks on top)
staging  = <scratch>/.remount-staging-<pid>-<n>
rollback = <scratch>/.remount-rollback-<pid>-<n>
workdir  = <run_dir>/work-remount-<n>          fresh; OLD's workdir is never reused

 1. unmask hidden daemon paths                     RemountMaskGuard (restore-on-drop)
 2. mount overlay NEW [l4, S(n3..n1)] at staging   production builder, real-path mode →
                                                   mountinfo shows lowerdir= (proof input)
 3. probe staging: read probe + exact lowerdir     ── fail → cleanup; OLD mount intact →
    list match, newest-first                          clean abort ("leased")
 ────────────── point of no return = first MS_MOVE RETURNS SUCCESS ──────────────
 4. MS_MOVE /workspace → rollback                  (a FAILED move here = clean abort)
 5. MS_MOVE staging    → /workspace
 6. restore masks; probe /workspace                ── fail ─┐  restore ladder: move new→staging,
 7. strict umount rollback — umount2(…, 0),        ── EBUSY ┴▶ rollback→root, re-probe OLD,
    NO lazy/MNT_DETACH fallback                                unmount staging.
                                                               verified ⇒ leased(pinned:rollback_unmount_busy)
                                                               unverified ⇒ FAULTY → report + destroy
 8. report: phase-tagged ALWAYS (first_move_succeeded present on error paths);
    mount_verified=true only when 2,3,4,5,6,7 ALL succeeded
```

Steps 1–7 execute while every non-allowlisted task that can observe the holder
mount namespace is frozen, so no lookup threads through the hidden-path
window. Step 7 succeeding is the proof the old mount had no residual users.
Old lease release never runs without `mount_verified=true`, active handle
persistence on the new lease, and task resume.

### C4. `/proc` inspection map (read while frozen; any read error = pinned)

```text
/proc/<pid>/task/<tid>/
├── stat        "4321 (bash) T … pgrp …"   membership snapshot (scan every task;
│               state T = stopped; Z excluded; 't' (ptrace-stop) requires
│               TracerPid ∈ frozen set, else quiesce_failed)
├── ns/mnt                                 must equal the holder mnt-ns inode for EVERY
│                                          discovered task → else pinned:mount_namespace_escaped
├── cwd  → /workspace/src                  dentry ref  → pinned:cwd_pinned_workspace
├── root → /                               chroot ref  → pinned:root_pinned_workspace
├── fd/9 → /workspace/build.log            open file   → pinned:fd_pinned_workspace
│    (allowlisted-safe anon fds only: PTY /dev/pts/*, socket:[…], pipe:[…],
│     eventfd, timerfd. io_uring, fanotify, and any OTHER anon_inode ⇒ pinned)
├── maps  path = bytes after the 5th       mmap        → pinned:mapped_file_pinned_workspace
│    whitespace field (paths contain       unparsable line or "(deleted)" ⇒ pinned
│    spaces; never "last column")
└── mountinfo  field 5 = mountpoint        must show the workspace overlay;
    (octal-escaped, e.g. \040 = space)     ANY child mount under the workspace root
                                           blocks — no exemptions (hidden-path masks
                                           are namespace-root tmpfs, not workspace
                                           children); post-switch: exact lowerdir=
                                           list proof
```

Blocked classes (contract-stable) and example details:

| class | example details |
| --- | --- |
| `unsupported` | platform, kernel gate not proven, `rewrite_degenerate_ledger` |
| `quiesce_failed` | `freeze_failed`, `freeze_timeout`, `membership_changed`, `tracer_outside_frozen_set` |
| `pinned` | `cwd_pinned_workspace`, `root_pinned_workspace`, `fd_pinned_workspace`, `mapped_file_pinned_workspace`, `child_mount_pinned_workspace`, `mount_namespace_escaped`, `rollback_unmount_busy`, `anon_inode_io_uring` |
| `mount_uncertain` | `mountinfo_unavailable`, `mountinfo_mismatch`, `proc_read_error`, `active_persist_failed` |
| `stage_failed` | `staging_mount_enospc`, `staged_probe_mismatch`, `lowerdir_limit` |

### C5. Failure policy

This table applies only to the post-commit remount sweep. Cleanup cannot fail
or roll back the already-committed squash. **No durable remount state
exists** — every rule below is in-process; at boot every session is dead and
handled identically by the three-step boot cleanup.

| Outcome | Rule |
| --- | --- |
| Abort before the first successful `MS_MOVE` (any cause: pins, escape, freeze budget, stage/probe failure, a failed first move) | Clean skip: release replacement lease, resume tasks, report `leased(class:detail)`. Session untouched — its workdir was never reused. |
| Switch verified end-to-end | Swap `MountedWorkspace` snapshot (+ fresh workdir path), persist active handle, resume tasks, release old lease (exclusive) → refcount GC. |
| Post-PONR failure, restore ladder verified | Release replacement lease, resume, report `leased(pinned:rollback_unmount_busy)`. Session back on the OLD mount, provably. |
| Post-PONR failure, restore unverified — or runner report missing/ambiguous at/past first-move-success | Report faulty (session id, phase, upperdir bytes discarded, lease-release errors), then destroy through the ordinary destroy path. Namespace death is the unmount proof; both leases release after it. |
| Active-handle persist failure after a verified switch | Resume **immediately** on the NEW mount (never hold tasks frozen on a persist retry); keep BOTH leases in memory, released at session destroy; report `leased(mount_uncertain:active_persist_failed)`. The old lease is never released without the durable handle (invariant 6). |
| Daemon crash at any point | The session died with the daemon (PDEATHSIG). Boot: reap run dir + handle, sweep to the active manifest. No remount-specific branching. |

Faulty reporting is not optional. The result/progress surface must include
the workspace session id, phase, upperdir bytes discarded, and lease-release
errors. Publishing or capturing uncertain upperdir state is not allowed;
there is no quarantine — the report is the record.

### C6. Namespace shell runner specifics

- Process-group plumbing already exists end to end and is useful as a seed:
  the shell runner installs every command in its own group via `setpgid(0,0)`
  before exec (`namespace-process/runner/shell_exec.rs`), the PTY records it,
  and `NamespaceExecution::pgid()` exposes it
  (`namespace-execution/execution.rs`). Quiesce still needs holder-scope task
  discovery (cgroup ∪ ns-scan ∪ allowlist); pgid-only freeze is not a
  correctness proof, and there is no pgid-specific blocked reason — pgid
  failures surface as `quiesce_failed` details.
- The holder and the pid-namespace init are **always** alive in the holder
  mount namespace; they and the remount runner form the infrastructure
  allowlist — exempt from freeze, still pin-inspected (cwd `/`, no workspace
  fds). "No observable tasks" means "discovered set ⊆ allowlist".
- **Interactive PTY bash** (driven via `write_command_stdin` /
  `read_command_lines`) runs with `current_dir` inside the workspace
  (`shell_exec.rs`), so frozen it is always `pinned:cwd_pinned_workspace` →
  always "leased". This is physics, not policy: MS_MOVE would leave its cwd
  dentry on the old overlay. Such sessions migrate on the first squash run
  after the shell exits, and one-shot sessions reclaim at their
  finalize-destroy anyway. They still take the uniform freeze → inspect path.
- **Batch/waiting commands** (sleeps, network waits, no cwd/fd/mmap/child
  mount under the workspace) can freeze with zero pins → live protocol applies
  and the session migrates without the command ever noticing. Current cwd
  validation keeps absolute cwd inside the workspace; do not use outside-cwd
  examples unless that validation changes.

---

## D. Space complexity — squash with vs. without remount

Notation:

| Symbol | Meaning |
| --- | --- |
| $B$ | base layer bytes |
| $P(t)$ | total bytes of published `L` layers **and prior-generation `S` layers** retained in history at time $t$ (so $F \le P$ holds across generations) |
| $F$ | flatten size — bytes of *surviving* content, $F \le P$; for rewrite-heavy workloads $F \ll P$. Staging bytes are counted in $F$ from build start (promotion is a rename) |
| $E$ | source entry count walked while flattening, including shadowed files and whiteouts |
| $U$ | Σ session upperdir bytes |
| $Q$ | sidecar/manifest temp bytes (KB-scale, ε) |
| $\Pi(t)$ | live-session retained space: old layer bytes kept for blocked/unswept sessions |
| $T_{sess}$ | lifetime of the longest-lived session; $T_{sweep}$ = sweep duration (seconds) |

Steady-state disk under $k$ long-lived sessions:

| | no squash | squash **without** remount | squash **with** remount |
| --- | --- | --- | --- |
| after squash commit | — | $B + P + F + U$ (worse than before!) | $B + P + F + U$ (same peak) |
| steady state | $B + P(t) + U$ | $B + P(t) + F + U$ — the whole $P$ stays pinned by live leases | $B + F + U + \Pi(t)$ + pins/singletons + publish tail — only sessions that pass remount shed old pins |
| after sessions end | $B + P + U'$ | $B + F + U'$ | $B + F + U'$ |
| peak duration | — | $O(T_{sess})$ | $O(T_{sweep})$ for clean sessions |
| reclaim latency | session destroy | **max session lifetime** | **seconds** (sweep); blocked: min(shell exit → next squash, destroy) |
| old-session lowerdir chain | $n{+}1$ | $n{+}1$ (unchanged — this is the killer) | clean sessions: rewritten chain length; blocked sessions: unchanged |
| extra work per squash | — | $O(E{+}F)$ flatten | $O(E{+}F)$ flatten $+ O(k)$ remount attempts, **0 bytes** copied by remount itself |

The complexity-class statement:

```text
without remount:  disk = Θ( B + history kept for the longest-lived session + F + U )
with remount:     disk = Θ( B + F + U + Π(t) + pin/singleton layers + publish tail since last squash )
```

The with-remount bound is not unconditional: a session leased after every
publish turns every layer into a pin or singleton, blocks vanish, and disk
degenerates to the no-squash Θ(B + P(t) + U). Dense pinning is the adversarial
floor of this design.

**Re-squash cost and write amplification.** Each generation's block ends at
the previous S layer (S layers are not boundaries — B4 re-squashes `[L8 Sa]`
into Sc), so flatten re-walks the full surviving content every run; with byte
copies, cumulative writes over $G$ generations would be $\Theta(G \cdot F)$.
Whole-file winners are therefore **hardlinked** from the immutable sources
(same filesystem, promote is same-fs rename), dropping per-generation cost to
$O(E)$ metadata ops plus bytes only for content that must be re-encoded
(whiteouts, opaques, partially-shadowed trees). `.bytes` sidecars count
logical bytes, so `du`-style views double-count hardlinked files — accepted
and noted in the observability view. The per-file fsync barrier during build
dominates wall clock for many-small-file layers (~1–10 s per 1000 entries).

Storage commit optimizes the active manifest immediately. The commit peak for
live-referenced runs is $B + P + F + U$ **with or without remount** — sources
cannot be deleted before commit, so the peak carries all of $P$, not just the
session-pinned subset $\Pi$; remount changes the peak's **duration**, not its
height. Fast publishes add to the new publish tail, and the run-presence
commit recheck keeps the race closed without starving. Squash is singleflight
per root, so peak temporary storage is one builder's staging.

Percentage examples below normalize **no squash** to `100%` for each workload
after subtracting common bytes. The denominator is the squash candidate run,
not total disk:

```text
space% = retained bytes for this squash candidate run / P0 * 100
```

This cuts common $B$, $U$, and layers outside the candidate run out of the
percentage, so the table shows the retained history effect directly — with the
caveat that candidate-run normalization **understates** retained space when
pins fragment the stack (tiny candidate runs, small denominators). Let $P_0$
be the candidate run's no-squash bytes, $F$ the new flattened layer bytes, and
$\Pi$ the candidate-run bytes still pinned by sessions that did not migrate.

| Case | Lease state for candidate run | Example shape | no squash | squash, no remount | squash + remount after sweep |
| --- | --- | --- | --- | --- | --- |
| same file rewritten 6 times | no live lease references run | $P_0=6s=600$ MiB, $F=s=100$ | `600` = **100%** | `100` = **17%** after GC | same; no remount needed |
| same file rewritten 6 times | live lease still references run | $P_0=600$, $F=100$ | `600` = **100%** | $P_0+F=700$ = **117%** until remount/session exit | $F+\Pi=100+0$ = **17%** if all pinning sessions migrate |
| 6 different files edited once | no live lease references run | $P_0=600$, $F=600$ | `600` = **100%** | `600` = **100%** after GC | same; byte-neutral, lowerdirs collapse |
| 6 different files edited once | live lease still references run | $P_0=600$, $F=600$ | `600` = **100%** | `1200` = **200%** until remount/session exit | `600` = **100%**; byte-neutral, lowerdirs collapse |
| create/delete temp churn | live lease still references run | $P_0=600$, surviving $F≈10$ | `600` = **100%** | `610` = **102%** until remount/session exit | `10` = **2%** |
| delete-heavy / opaque dirs | live lease still references run | $P_0=500$, surviving $F≈50$ | `500` = **100%** | `550` = **110%** until remount/session exit | `50` = **10%** |
| many small layers, distinct content | live lease still references run | $P_0=400×1$ MiB, $F=400$ | `400` = **100%**, `400` lowerdirs | `800` = **200%** until remount/session exit | `400` = **100%**, lowerdirs collapse |
| mixed live sessions | some sessions migrate, some stay pinned | $P_0=600$, $F=100$, $\Pi=300$ | `600` = **100%** | `700` = **117%** until all sessions exit | `400` = **67%**; only blocked sessions keep old pins |

So squash without remount is already enough when no live lease references the
candidate run. Remount matters only for live-referenced runs: it lets squash
reclaim old candidate-run bytes before the session exits. For append-only
distinct content, remount mainly reduces lowerdir depth and lease cleanup cost,
not bytes.

**Chain-length limits (numeric).** Two different caps apply:

- **Creation path** (fd-based new-mount API): overlayfs `OVL_MAX_STACK` =
  **500** lowerdirs. Workspace creation past 500 layers fails regardless of
  squash; bounding the active chain still requires actually invoking squash —
  there is no trigger policy.
- **Staged remount path** (real-path legacy option string, one-page limit):
  ≈ ⌊(4096 − ~110 overhead) / entry⌋ ≈ **97** lowerdirs at the default root
  `/eos/layer-stack` (41 bytes per entry); fewer for longer roots. Rewritten
  chains are bounded by ≤ 2k+2 for k plan-time pins, so remount effectiveness
  collapses near k ≈ 50 concurrently pinning sessions. An over-limit staged
  mount fails the exact lowerdir-list probe as a clean pre-PONR
  `stage_failed:lowerdir_limit` — silent option-string truncation is caught by
  the probe, which doubles as the limit detector — and leaves the old lease
  intact.

**Sweep time budget.** Each attempt is freeze $O(\text{procs})$ + poll +
inspection $O(\text{procs} \times \text{fds})$; a per-session **freeze budget**
(e.g. 500 ms) bounds D-state stragglers via `quiesce_failed:freeze_timeout`
and the sweep proceeds. Total sweep duration = $O(\sum \text{procs} + \sum
\text{fds})$ + $k$ staged mounts + GC. Lease-release GC membership checks must
be set-based — $O(k \cdot n \log n)$ total, not the $O(k^2 n^2)$ a
`Vec::contains` scan would cost inside the writer lock.

---

## Required tests

Unit/integration:

1. `partition_blocks_between_pins_and_base` — boundaries from
   `lease_newest_layers()`; singleton runs; reclaim-vs-leased classification
   comes from the commit GC result, not plan-time snapshots.
2. `flatten_matrix` — whiteout encodings (char-dev and xattr fallback both
   re-emitted correctly), opaque markers, shadowed subtrees dropped unless the
   winner, **dir-created-then-emptied survives**, file modes preserved,
   whole-file winners hardlinked, no-follow walks through malicious symlinks.
3. `commit_gc_never_deletes_layers_leased_after_plan` — a workspace lease
   acquired between plan and commit ⇒ block reports `leased`, source dirs
   survive, the new session's mount stays healthy.
4. `commit_recheck_compacts_through_racing_publish_or_conflicts_cleanly` —
   continuous publish loop; commit succeeds via run-presence with
   `version = latest + 1`; no starvation; broken run ⇒ `manifest_conflict`.
5. `squash_singleflight_per_root` — a second invocation waits or fails
   cleanly; staging names are nonce-minted; no interleaved builders.
6. `crash_and_error_paths_around_commit` — crash after promote/metadata but
   before manifest rename: restart keeps the old manifest and boot sweeps the
   orphan S + all three sidecars; a *non-crash* post-promote failure removes
   the promoted S dir + sidecars in-process (no orphan awaiting restart).
7. `staging_tree_durability` — every directory and whiteout in the staging
   tree is fsynced bottom-up before promote (fsync-recording shim); simulated
   power-fail after commit leaves S content/whiteouts/symlinks intact.
8. `ledger_expand_then_contract_matrix` — B4's ws-2/ws-3 shapes including the
   generation-crossing contraction; reclaimed inner S; missing, unknown-version,
   and degenerate (`|sources| < 2`, mutually recursive) ledgers ⇒ identity in a
   single bounded pass, never a wrong chain, never a hang.
9. `admission_blocks_all_workspace_session_entrypoints` — the per-session gate
   blocks exec launch, one-shot create/finalize **including the
   timeout/cancel completion hook firing mid-switch** (SIGKILL of the frozen
   pgid must not let finalize's capture/destroy interleave with the MS_MOVE
   pair), file ops, capture, destroy, and runner entrypoints; destroy waits
   until the attempt resolves; session-gone at the gate ⇒ silent skip with no
   leaked replacement lease.
10. `retarget_never_runs_before_mount_verification` — injected staged/visible
    probe failure ⇒ old lease manifest unchanged, replacement lease released.
11. `post_commit_remount_failure_does_not_fail_squash_commit` — freeze/stage
    failure before PONR reports `leased`, keeps the old lease, committed
    manifest intact.
12. `persist_failure_keeps_both_leases` — verified switch + injected
    active-handle persist failure ⇒ tasks resume immediately on the NEW mount,
    both leases held and released at session destroy; the old lease is never
    released without the durable handle.
13. `old_layers_not_deleted_until_refcount_zero` — a shared run pinned by a
    second lease survives the first migration.
14. `boot_cleanup_matrix` — missing/unparsable `manifest.json` ⇒ nothing
    deleted (fail closed, `B*` and mount boundaries respected); with a valid
    manifest: leftover run dirs and handles reaped, then sweep keeps exactly
    the active manifest's layers/sidecars.
15. `faulty_outcome_is_reported_then_destroyed` — post-PONR failure with
    unverified restore and non-empty upperdir ⇒ report carries session id,
    phase, upperdir bytes discarded, lease-release errors; session destroyed
    via the ordinary path; leases release only after namespace death.
16. `squash_output_contract` — three-field JSON; `leased` carries the lease
    count and distinct `class:detail` strings; empty `squashed_blocks` when
    nothing to do; faulty outcomes visible in the agreed result/progress
    surface.
17. `checkpoint_squash_manager_cli_forwards_to_runtime` — manager catalog
    exposes `checkpoint_squash --sandbox-id`; runtime catalog does not expose
    `squash_layerstack`; forwarding reuses the existing endpoint/client path.
18. `ultra_nonfaulty_sweep_converges` — B5 end-to-end: live migration under a
    running command, identity short-circuit, cwd-pinned clean abort,
    `stage_failed` clean abort, zero faulty outcomes, full convergence on the
    following invocation with no persisted sweep state.

Live Docker e2e (required before enabling live remount):

1. `same_upperdir_fresh_workdir_kernel_gate` — OLD and NEW overlays coexist
   with the same upperdir and a fresh NEW workdir, production-equivalent
   options, staged `MS_MOVE`, visible probe, strict rollback unmount; **after a
   staged mount + abort, OLD still copy-ups** (this is the test that fails if
   the workdir is shared, and it gates live remount off).
2. `real_path_mode_parity_includes_userxattr_whiteouts` — staged NEW built by
   the production builder in real-path mode shows the exact newest-first
   lowerdir list in mountinfo AND does not resurrect a file deleted on OLD
   (a helper without `userxattr` — the removed code's behavior — fails this).
3. `all_task_quiesce_blocks_escaped_pgid_child` — child changes pgid/session
   while sharing the holder namespace; discovery (cgroup ∪ ns-scan) still
   finds and freezes it.
4. `nested_mount_namespace_blocks_remount` — `unshare -m sleep inf` inside the
   session ⇒ `pinned:mount_namespace_escaped`, no MS_MOVE attempted, old
   layers retained.
5. `hidden_masks_not_visible_during_remount` — daemon paths are not observable
   by any live task during unmask/remask.
6. `proc_pin_matrix_blocks_uncertainty` — per-task cwd, root, fd, mmap of a
   path containing spaces (`/workspace/a b.txt` — offset parsing), unmanaged
   child mount, io_uring anon fd, outside tracer on a `t` task, mountinfo
   escaping, permission failure, and process churn each block with a stable
   `class:detail`.
7. `strict_unmount_and_restore_ladder` — an SCM_RIGHTS-parked workspace fd
   survives inspection; strict `umount(rollback)` returns EBUSY; the restore
   ladder verifies ⇒ `leased(pinned:rollback_unmount_busy)`, session resumes on
   OLD with working reads and copy-up; with restore verification forced to
   fail ⇒ faulty report + destroy.
8. `ponr_boundary_and_phase_report` — injected EINVAL on the first MS_MOVE ⇒
   `first_move_succeeded=false`, clean `leased(stage_failed:…)`; runner killed
   at three points (after unmask, between the moves, after visible probe) ⇒
   pre-move-proof reports abort clean, missing/ambiguous reports at/past
   first-move-success go faulty.
9. `lowerdir_limit_matrix_staged_vs_create` — measured staged real-path limit
   (≈ page/pathlen) vs creation `OVL_MAX_STACK`; over-limit rewritten chain ⇒
   `stage_failed:lowerdir_limit` cleanly on every run with the old lease
   intact; creation at 501 layers fails with a distinct documented error.
10. `crash_matrix_recovery` — daemon killed at every C5-relevant point
    (mid-freeze, mid-switch, before old-lease release); PDEATHSIG kills the
    holder in every case; restart runs reap-then-sweep, keeps exactly the
    active manifest's layers, and never resurrects session state.
