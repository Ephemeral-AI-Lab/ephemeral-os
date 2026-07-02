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

## Goal

Bound LayerStack storage and lowerdir chain length by squashing published
layers into equivalent flattened layers, and migrate **live** workspace
sessions onto the compact chains so old layers reclaim immediately instead of
at session destroy.

Policy (inherits the verdict of the removed live-remount experiment,
`docs/layerstack-command-lease-live-remount_SPEC.md` @ `b6a1de0ac`, remapped
onto the current architecture where the shell runner lives in
`namespace-execution`):

```text
Squash squashes every squashable block; zero options, zero trigger policy.
Never mutate or delete a layer directory any live lease references.
Live remount only after kernel-gated mount proof, all-entrypoint admission,
all-task quiesce, and zero-pin proof.
Retarget lease metadata only as a record of a verified mount state.
Blocked session = healthy: keep its lease, next squash catches it.
Failed session past the point of no return = faulty: report and destroy or
quarantine explicitly.
```

Live remount is gated, not assumed. If any gate below is not proven in the
supported Docker sandbox environment, squash still commits compact layers but
leaves live sessions leased:

1. **Same-upperdir staged mount proof**: OLD and NEW overlays may coexist with
   the same upperdir/workdir long enough for the staged `MS_MOVE` switch, with
   production-equivalent options (`userxattr`, no `index`) and exact lowerdir
   verification.
2. **All-task quiesce proof**: every task that can observe the holder mount
   namespace is stopped or absent. A command pgid is only a seed, never the
   whole proof.
3. **Startup reconciliation proof**: persisted workspace handles and pending
   remount attempts are reconciled or reaped before any boot storage sweep.

CLI surface (one operation, no arguments):

```text
sandbox-cli runtime squash_layerstack
```

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
      "blocked_reasons": ["cwd_pinned_workspace"] }
  ]
}
```

`layers` = the current stack (a); `squashed_blocks` = this run's blocks vs
lease state (b). No `no_op`, no boundaries dump, no byte totals — byte
accounting stays with the daemon `layerstack` observability view.

## Vocabulary and invariants

| Name | Meaning |
| --- | --- |
| `SquashBoundary` | `Base` (never squashed), `LeasePin` (a layer that is the newest layer of some live lease's manifest). |
| `SquashBlock` | Maximal contiguous run of ≥ 2 active-manifest layers with no boundary inside. |
| `flatten` | Pure fold of a block's layer dirs into one changeset: newest-wins per path, non-dir winners and opaque markers mask older entries under them, whiteouts/opaques always retained; source walks are fd-relative and no-follow. |
| `SquashedLayer` | The one new layer per block; id prefix `S` (`S000014-2a`). |
| `LayerSubstitution` | Persisted ledger `<S-id>.sources` with schema version, replaced run, source manifest version/root hash, written at commit. Missing or unknown ledgers fail closed for remount rewrite. |
| rewritten manifest | A lease manifest with every fully-contained substitution applied to fixpoint; same logical snapshot, shorter chain. |
| `acquire_replacement_lease` | Under the writer lock: validate rewritten layers alive and acquire a new lease. Does not release the old lease. |
| `release_replaced_lease_after_verified_switch` | Under the writer lock after visible mount verification and Active persistence: release the old lease and run existing refcount GC. |
| `RemountState::Pending` | Durable attempt record `{attempt_generation, phase, old_lease, new_lease, old_snapshot, new_snapshot}` persisted before the first `MS_MOVE`; boot recovery reaps or cleans from this record, never guesses. |
| quiesce | Stop every task that can observe the holder mount namespace, poll `/proc/*/stat` to `T`, verify membership stable. The command pgid is only a discovery seed. |
| pin | A frozen task's `cwd`/`root`/open fd/mapped file/child mount inside the workspace mount, or any inspection uncertainty. Any pin ⇒ no live switch. |
| staged switch | Kernel-gated mount of new overlay at staging, probe, MS_MOVE old→rollback, MS_MOVE staging→root, re-mask, probe, unmount rollback. |
| point of no return | The first MS_MOVE. Failures before it: session untouched. At/after it: session is faulty and must be reported, then destroyed or quarantined by explicit policy. |

Invariants:

1. **Merged-view equivalence** — for every path, the merged view of the
   rewritten/post-squash manifest equals the pre-squash view. Squash changes
   structure, never content; remount is invisible to session semantics.
2. **Pin-overlap** — the replacement lease is acquired before the old one is
   released; no instant exists where either chain is unpinned. Clean aborts
   release only the replacement lease.
3. **Never-straddle** — lease manifests are contiguous history prefixes and
   blocks never cross plan-time pins, so every substitution is fully-inside or
   fully-disjoint for every lease, any generation. Every session is always
   rewritable (possibly to identity).
4. **Detach-before-delete** — old lowerdirs are deleted only via lease
   release after the old mount is unmounted (or its namespace died).
5. **Upperdir sanctity** — remount never touches upperdir/workdir; uncommitted
   writes survive byte-for-byte only after the same-upperdir kernel gate proves
   OLD and NEW can coexist with identical mount options (`userxattr`, no
   `index`). Without that proof, live remount is disabled.
6. **Ordering contract** (from the historical spec, kept verbatim):
   `build → stage → verify staged → switch → verify visible → unmount rollback
   → retarget lease → refcount GC → resume`. Never retarget first.
7. **Session lifecycle is not squash's business** — remount never
   creates/captures/publishes. The single exception: a session left in an
   uncertain mount state past the point of no return is reported as faulty and
   then destroyed or quarantined by explicit policy.

---

## A. Expected file/folder structure with LoC change

`(new ~N)` = new file with estimated LoC; `(+N)` = lines added to existing
file. Calibrated against existing module sizes (`publish/plan.rs` 241,
`publish/model.rs` 147, service impls 26–64).

```text
crates/sandbox-runtime/layerstack/
├── src/stack/squash/mod.rs                  (new ~180)  squash_layers: plan → build → commit → reclaim
├── src/stack/squash/model.rs                (new ~100)  SquashBlock{Plan,Outcome}, SquashOutcome, LayerSubstitution
├── src/stack/squash/partition.rs            (new  ~70)  pins + base → blocks (pure)
├── src/stack/squash/flatten.rs              (new ~170)  layer-dir walk → masked Vec<LayerChange> (pure)
├── src/stack/squash/rewrite.rs              (new  ~80)  ledger load + fixpoint manifest rewrite (pure)
├── src/stack/lease/replacement.rs           (new  ~70)  acquire replacement lease; release old after verified switch
├── src/storage/sweep.rs                     (new  ~70)  boot sweep of unreferenced layers/staging/metadata
├── src/storage/fs.rs                        (+30)       metadata dir read/write helpers
├── src/stack/lease/cleanup.rs               (+8)        remove whole metadata dir beside layer dir
├── src/{lib,stack/mod,stack/lease/mod,storage/mod}.rs   (+25)  wiring + exports
└── tests/unit/squash.rs (new ~380) · tests/unit.rs (+1)

crates/sandbox-runtime/overlay/
├── src/legacy_mount.rs                      (new ~120)  kernel-gated visible-options mount (mountinfo
│                                                        lowerdir= proof, production option parity),
│                                                        move_mountpoint, unmount_overlay
├── src/{lib,kernel_mount}.rs                (+7)        exports; peel visibility
└── tests/unit/legacy_mount.rs (new ~70) · tests/unit.rs (+1)

crates/sandbox-runtime/namespace-execution/
├── src/quiesce.rs                           (new ~230)  all-task holder-scope quiesce: pgid seed,
│                                                        task discovery, SIGSTOP, poll-stopped,
│                                                        /proc pin inspection, resume-on-drop guard
├── src/engine.rs                            (+30)       engine.remount_overlay beside mount_overlay
├── src/lib.rs                               (+3)
└── tests/{quiesce.rs (new ~80), engine.rs (+25)}

crates/sandbox-runtime/namespace-process/
├── src/runner/setns/remount_overlay.rs      (new ~250)  staged switch: RemountMaskGuard unmask/restore,
│                                                        staging+rollback mountpoints, probes, MS_MOVE pair,
│                                                        rollback-unmount, verification report
│                                                        (port of removed runner/setns.rs remount body)
├── src/runner/{setns,protocol,mod}.rs       (+30)       op entry, request fields, dispatch
└── tests/unit/runner/setns.rs               (+60)

crates/sandbox-runtime/workspace/
├── src/lifecycle/remount.rs                 (new ~110)  remount transaction: persist RemountState::Pending →
│                                                        apply → verify → swap snapshot + persist_handles →
│                                                        Active; faulty report + destroy/quarantine
├── src/service/impls/remount_workspace.rs   (new  ~95)  rewrite → replacement lease → runtime remount
├── src/namespace/setns_runner.rs            (+35)       NamespaceRuntime::remount_overlay
├── src/session/state.rs                     (+14)       RemountState::{Active,Pending} on MountedWorkspace
├── src/lifecycle/persistence.rs             (+6)        persist remount_state; boot recovers Pending by phase
├── src/{model,service,service/impls/mod,lib}.rs         (+42)
└── tests/unit/remount.rs (new ~170) · tests/unit.rs (+1)

crates/sandbox-runtime/operation/
├── src/layerstack/service/impls/squash.rs   (new  ~75)  span layerstack.squash, error mapping
├── src/workspace_session/service/impls/remount_session.rs (new ~95)  admission + session-registry snapshot refresh
├── src/cli_definition/layerstack_operations.rs (new ~110) family "layerstack", zero-arg spec, report JSON
├── src/command/service/core.rs              (+10)       route command entrypoints through session admission
├── src/services.rs                          (+85)       sweep: idle / clean-live / pinned / faulty paths
├── src/layerstack/service/{model,mod}.rs, src/workspace_session/…/mod.rs,
│   src/{operation,cli_definition/mod}.rs    (+45)       DTOs, exports, registration
└── tests/layerstack_squash.rs (new ~300) · tests/support/mod.rs (+25)

crates/sandbox-observability/
└── src/record.rs                            (+8)        LAYERSTACK_SQUASH, WORKSPACE_SESSION_REMOUNT,
                                                         NAMESPACE_EXEC_REMOUNT_OVERLAY

sandbox-protocol / sandbox-daemon / sandbox-gateway / sandbox-manager   (+0)
```

Totals: **15 new source files ≈ 1,725 LoC**, **≈ +425 LoC** in existing files,
**≈ 1,300 LoC** of tests → ≈ 3,450 LoC end to end.

Build order: layerstack squash modules (pure parts first) → overlay legacy
primitives → namespace-execution quiesce → namespace-process staged runner →
workspace transaction → operation sweep + CLI spec.

## Storage layout and transaction

Squash creates exactly **one temp dir per block** under `staging/`; remount
creates **zero layers** (its disk writes are the atomic `manager.json` rewrite
and the durable `RemountState` phase update).

```text
<layer_stack_root>/
├── manifest.json                       the ONLY commit point (write_atomic: tmp → rename → dir fsync)
├── workspace.json                      binding — never touched
├── .storage-writer.lock                flock: one owning process per root
├── layers/
│   ├── B000001-base/                   never touched (may be absolute/shared)
│   ├── L… (pins, singletons)           never touched
│   ├── L… (block sources)              deleted at reclaim, never before
│   └── S000014-2a/                     ← promoted from staging by same-fs rename(2)
├── staging/
│   └── S000014-2a.staging/             ← the temp layer: flatten output (files copied
│                                         from pinned sources, whiteouts, opaque markers)
└── .layer-metadata/
    └── S000014-2a/
        ├── digest
        ├── bytes
        └── sources.json                substitution ledger (remount input)
```

| # | Phase | Lock | Disk mutation | Crash here | Cleaner |
| --- | --- | --- | --- | --- | --- |
| 1 | plan + pin lease | shared, brief | none | — | — |
| 2 | build blocks | none (pin lease pins sources) | staging dirs, fsynced | orphan staging | error path; boot sweep |
| 3 | verify blocks contiguous | exclusive | none | — | conflict ⇒ abort clean |
| 4 | promote | exclusive | rename → `layers/S…` | unreferenced S dir | boot sweep |
| 5 | metadata | exclusive | metadata dir with `digest`/`bytes`/`sources.json` | unreferenced metadata | boot sweep |
| 6 | **COMMIT** | exclusive | `manifest.json` atomic replace | committed; old runs linger | boot sweep |
| 7 | reclaim | exclusive | release pin lease → delete unpinned runs | partial deletes, unreferenced | boot sweep |
| 8 | remount sweep | session admission lock | `manager.json`; `RemountState`; kernel mounts | phase-specific recovery | restart reconciliation |

Boot sweep (`sweep_unreferenced_storage`, once at daemon start) runs only after
workspace handle reconciliation:

1. Load active manifest.
2. Load persisted `manager.json` handles. Legacy handles without
   `RemountState` are treated as Active handles with their persisted lease and
   `layer_paths`.
3. Recreate lease pins for live/recoverable handles, or reap the handle before
   storage sweep.
4. Resolve every `RemountState::Pending` by phase: clean pre-PONR abort releases
   the replacement lease; unknown or post-PONR state reaps/quarantines the
   workspace by faulty policy.
5. Only then delete unreferenced `staging/`, `layers/*`, and metadata dirs not
   referenced by the active manifest, live/recreated leases, Pending records, or
   unreaped persisted handles. Base/shared absolute layers are never swept by
   this root.

Metadata cleanup deletes the whole `.layer-metadata/<layer-id>/` directory,
not one sidecar at a time.

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
│ L2 │  ├── one block ──▶  S1 = flatten(L3,L2,L1) │ S1 │           L3,L2,L1 deleted
│ L1 │ ─┘                                         ├────┤           AT COMMIT (pinning
├────┤                                            │ B  │           set is empty)
│ B  │                                            └────┘
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
│ L3 │  ├─ block ─▶ S1   pinning set {ws-1}    │ S1 │    lease [L5,L4..L1,B] →
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
active v13 (newest→base)    boundary          block/pinning set        active v14
┌─────┐
│ L12 │                      —                 ┌ singleton? no: ┐      ┌─────┐
│ L11 │ ─┐                                     │ {} (nobody     │      │ L12 │
│ L10 │ ─┴─────────────────────────▶ S1        │  leased ≥ v10) │      │ S1  │
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
commit:  L11,L10 deleted immediately (empty pinning set)

sweep:
  ws-A [L9 L8 L7 L6 L5 L4 L3 L2 L1 L0 B] ── S2 ✓, S3 ✓ ──▶ [L9 S2 L5 L4 L3 S3 B]   migrated
  ws-B [L5 L4 L3 L2 L1 L0 B]             ── S3 ✓        ──▶ [L5 L4 L3 S3 B]         migrated
  ws-C [L3 L2 L1 L0 B]                   ── S3 ✓        ──▶ [L3 S3 B]               migrated
  ws-D shell frozen → cwd_pinned_workspace → SIGCONT, lease untouched               leased

reclaim cascade:      L11 L10 │ L8 L7 L6 │ L2 L1 L0
  commit               DELETED│ pinned:A │ pinned:A,B,C,D
  ws-A migrates               │ DELETED  │ pinned:B,C,D
  ws-B migrates               │          │ pinned:C,D
  ws-C migrates               │          │ pinned:D   ← report: S3 "leased", 1 lease
  ws-D shell exits / next squash / destroy            → DELETED
```

Every substitution is fully-inside or fully-disjoint per lease
(never-straddle), so each rewrite either applies whole or skips whole.

### B4. Ultra-complex — two generations, ledger fixpoint, races, faulty session

```text
gen-1  active v9: [L8 L7 L6 L5 L4 L3 L2 L1 B]
       ws-1 @v8 (interactive shell, cwd-pinned)  ws-2 @v4 (idle)
       pins: L8(ws-1), L4(ws-2)  →  blocks [L7 L6 L5]→Sa, [L3 L2 L1]→Sb
       commit v10: [L8 Sa L4 Sb B]
       sweep: ws-2 → [L4 Sb B] (Sb applied); L3..L1 still pinned by ws-1 → keep
              ws-1 → cwd_pinned_workspace → leased (everything it pins stays)

t1     ws-1 shell exits; user destroys ws-1 → its release frees L7 L6 L5 L3 L2 L1
       publishes land: v12 = [L11 L10 L8 Sa L4 Sb B]
       ws-3 created @v10-era manifest (pins L10…); runs a CLEAN batch cmd
       (network wait, no cwd/fd/mmap/child mount under workspace)

gen-2  pins: L10(ws-3), L4(ws-2)
       blocks: [L11] singleton→kept · [L8 Sa]→Sc (RE-SQUASH of a prior S) ·
               [Sb] singleton→kept
       mid-build race: publish L12 lands → commit verify still passes
       (publishes only prepend); commit v13: [L12 L11 L10 Sc L4 Sb B]
       ledger now: Sa.sources, Sb.sources, Sc.sources=[L8,Sa]

       sweep:
         ws-3 live protocol: freeze → ZERO pins → staged switch → verify →
              rewrite fixpoint: Sa ✓(inside) then Sc ✓ → […L10 Sc L4 Sb B]
              → persist Active(new) → release old lease → L8, Sa deleted → SIGCONT
                                                                    migrated (live)
         ws-2 rewrite: Sc.sources=[L8,Sa] ⊄ [L4 Sb B] → identity → untouched
         ws-4 (hypothetical): clean pins, but rollback-unmount fails AFTER the
              MS_MOVE pair → past the point of no return → FAULTY; explicit
              destroy/quarantine policy handles teardown → lease release
              reclaims everything only it pinned                 faulty

crash  daemon dies between promote and manifest write of some gen:
       boot sweep deletes the orphan layers/S… + metadata; a persisted
       RemountState::Pending handle is recovered by phase before any layer
       sweep: clean pre-PONR phases release the replacement lease; uncertain
       or post-PONR phases reap/quarantine the workspace (never assumed
       migrated).
```

The ledger fixpoint is what lets a lease created in gen-0 cross gen-1 and
gen-2 substitutions in one rewrite; a substitution whose `S` layer has since
been reclaimed is skipped by replacement-lease validation (best-effort,
worst case identity).

### B5. Ultra-complex, non-faulty — live protocol under stress, clean aborts only

Same world as B4 generation 2 (post-commit v13 = `[L12 L11 L10 Sc L4 Sb B]`,
`Sc.sources = [L8, Sa]`), but the sweep hits every hard path and still ends
with **zero faulty sessions** — "faulty" is a narrow, provable condition,
not a synonym for "something went wrong".

```text
sweep over four sessions:

ws-3  batch cmd waiting on a socket, no workspace pins        FULL LIVE PROTOCOL
      freeze: all-task stop → /proc poll → all 'T' in ~40 ms
      inspect: cwd not under workspace ✓ · root / ✓ · fds {socket:[…], /dev/null} ✓ ·
               maps {libc,…} ✓ · mountinfo shows /workspace ✓  → ZERO pins
      rewrite fixpoint: [L10 L8 Sa L4 Sb B] → [L10 Sc L4 Sb B]
      stage NEW → probe ✓ → MS_MOVE pair → masks ✓ → probe ✓
      umount rollback ✓         ← the PROOF STEP PASSES: nobody held the old mount
      persist Active(new) → release old lease → L8, Sa deleted → SIGCONT
      the command never notices: a socket fd is not a workspace object,
      absolute lookups re-resolve onto the new mount, and upperdir writes
      made before the freeze are still visible after         → migrated (live)

ws-2  rewrite is identity (Sc.sources ⊄ [L4 Sb B])
      short-circuits before any freeze                       → untouched

ws-5  interactive PTY bash at prompt, cwd=/workspace/src
      freeze ✓ → inspect: cwd pin → SIGCONT within ~50 ms    → leased
      (cwd_pinned_workspace; the shell never observes anything)

ws-6  clean batch cmd, BUT the staging mount fails ENOSPC
      (the transient commit peak B+Π+F filled the disk)
      failure is BEFORE the point of no return — the old
      mount was never moved → SIGCONT → session untouched    → leased (stage_failed)
      NOT faulty: contrast B4's ws-4, whose
      failure came after the MS_MOVE pair

next invocation, minutes later (ws-3's migration freed space; ws-5's shell
has exited):
      ws-5 → plain switch → migrated · ws-6 → live protocol → migrated
      their old runs delete; the stack converges with no retry machinery,
      no persisted sweep state, zero faulty outcomes across both runs
```

The line between B5 and B4 is exactly one syscall: B5's `umount(rollback)`
succeeded (the zero-pin proof held), B4's returned EBUSY (the proof was
falsified). Everything before that boundary aborts clean and reports
`leased`; only beyond it does "faulty ⇒ explicit destroy/quarantine policy" exist.

## CLI `squash_layerstack` output examples

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

**Nothing to squash** (replaces any `no_op` flag — the state speaks for
itself):

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
      "blocked_reasons": ["cwd_pinned_workspace"] }
  ]
}
```

**B5 — ultra-complex non-faulty: multiple leases, distinct reasons on one
block** (ws-3 migrated so it no longer pins; ws-5 and ws-6 still do):

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
      "blocked_reasons": ["cwd_pinned_workspace", "stage_failed"] }
  ]
}
```

**Faults — one JSON line on stderr, exit 1.** A racing squash lost the
commit verification (safe to re-run; the retry re-plans):

```json
{"error":{"kind":"manifest_conflict","message":"active manifest changed: expected version 13, found version 14","details":{"expected_version":13,"found_version":14}}}
```

Anything else (build I/O failure, replacement-lease validation, runner failure):

```json
{"error":{"kind":"operation_failed","message":"layer-stack storage error: …","details":{}}}
```

In fault cases the manifest is untouched unless the commit point (transaction
step 6) was reached; partial progress before it is never referenced state.

**With `--progress`** — after runtime progress forwarding is wired,
build/sweep telemetry streams on stderr via the existing `cli_log` channel
(the same one the base-layer build feeds); the result stays one line on
stdout. A faulty-session outcome must appear in this agreed result/progress
surface with session id, phase, upperdir bytes, quarantine/discard, and lease
errors; it must not be observability-only:

```text
$ sandbox-cli runtime --sandbox-id sb-1 squash_layerstack --progress
[progress 0.102s] squash plan: 1 block, 2 source layers, 2 pins
[progress 1.211s] flatten S000013-3c: copied files=1204 bytes=18320411
[progress 1.540s] commit: manifest v12 -> v13
[progress 1.902s] remount ws-3: frozen 3 procs, 0 pins, staged switch verified
[progress 2.480s] remount ws-5: blocked cwd_pinned_workspace, resumed
[progress 2.815s] remount ws-4: rollback unmount EBUSY -> faulty phase=rollback_unmount upperdir_bytes=1832 action=quarantine
[progress 3.407s] remount sweep: 1 migrated, 2 leased, 1 faulty
[Output]
{"manifest_version":13,"layers":[...],"squashed_blocks":[...]}
```

---

## C. Remount workflow — workspace + namespace shell runner

### C1. Sweep decision tree (per session, inside the squash operation)

```text
                lock workspace-session admission
                (blocks exec, one-shot create, file ops, capture, destroy,
                 namespace runner entrypoints, and remount)
                                    │
        ┌───────────────────────────┼──────────────────────────────┐
   no observable tasks        all-task freeze+inspect          freeze/inspect finds pins,
        │                     proves ZERO pins                 child mounts, or uncertainty
        ▼                          │                                │
  plain staged switch         full live protocol               SIGCONT immediately
  (same admission lock;       (C2/C3 below)                    release replacement lease,
   no freeze needed)               │                           lease untouched
        │                          │                                │
        ▼                          ▼                                ▼
    migrated                   migrated                     "leased" + blocked_reason
                                                            (caught by NEXT squash run —
                                                             no retry machinery exists)
   any failure past the point of no return, any path ──▶  faulty report +
                                                         destroy/quarantine
```

### C2. Full sequence — crate swimlanes

```text
operation/services.rs   workspace-session     namespace-execution   workspace crate      namespace-process runner        layerstack
──────────────────────  ────────────────────  ────────────────────  ───────────────────  ─────────────────────────────  ─────────────────
squash committed
for each session:
  begin_remount ───────▶ admission lock + attempt_generation
  rewrite lease manifest ──────────────────────────────────────────────────────────────────────────────────────▶ rewrite_with_
                                                                                                                  substitutions
  acquire replacement lease ────────────────────────────────────────────────────────────────────────────────────▶ acquire_replacement_lease
  persist Pending ─────────────────────────────────▶ RemountState::Pending {
                                                     phase=quiescing,
                                                     old_lease,new_lease,
                                                     old_snapshot,new_snapshot,
                                                     attempt_generation
                                                   }
  discover live tasks ─▶ task set from holder namespace/cgroup, pgid seed only
  freeze ─────────────────────────────▶ quiesce.rs:
                                        stop all discovered tasks, poll /proc
                                        stat until all 'T', verify stable set
  inspect ────────────────────────────▶ /proc pins + child mounts (C4)
  (pins/uncertainty → persist Active(old), release replacement lease,
   SIGCONT, unlock, next session)
  persist Pending(phase=ready_to_switch)
  staged switch ─────────────────────────────────────────────────────────▶ setns user+mnt →
                                                                           staged switch (C3) → report
  verify report ok → swap MountedWorkspace.snapshot
                     + WorkspaceSession.handle.snapshot,
                     persist_handles → RemountState::Active
  release old lease ────────────────────────────────────────────────────────────────────────────────────────────▶ release_replaced_lease_after_verified_switch
                                                                                                                  → refcount GC
  SIGCONT ────────────────────────────▶ resume guard
  unlock admission
  report-not-ok before PONR ──▶ persist Active(old), release replacement lease,
                                SIGCONT, unlock, "leased" + reason
  report-not-ok after PONR ───▶ persist Faulty, destroy/quarantine session,
                                release leases according to persisted phase
```

### C3. The staged switch (inside the holder's mount namespace)

Preconditions:

- same-upperdir/workdir kernel gate passed in live Docker;
- visible-options mount helper matches production options (`userxattr`, no
  `index`) and exposes exact lowerdir proof;
- all-task quiesce proof holds, or the workspace has no observable tasks.

```text
/workspace ── overlay OLD [l4, n3, n2, n1] (hidden-path masks on top)
staging  = <scratch>/.remount-staging-<pid>-<n>
rollback = <scratch>/.remount-rollback-<pid>-<n>

 1. unmask hidden daemon paths                     RemountMaskGuard (restore-on-drop)
 2. mount overlay NEW [l4, S(n3..n1)] at staging   legacy visible-options mount →
                                                   mountinfo shows lowerdir= (proof input)
 3. probe staging: read probe + exact lowerdir     ── fail → cleanup; OLD mount intact →
    list match, newest-first                          clean abort ("leased")
 ───────────────────────── point of no return ─────────────────────────
 4. MS_MOVE /workspace → rollback
 5. MS_MOVE staging    → /workspace
 6. restore masks; probe /workspace                ── fail ┐
 7. umount rollback (old overlay released)         ── fail ┴▶ session FAULTY → report + destroy/quarantine
 8. report: mount_verified=true only when 2,3,5,6,7 ALL succeeded
```

Steps 1–7 execute while every task that can observe the holder mount namespace
is frozen (or no such task exists), so no lookup threads through the hidden-path
window. Step 7 succeeding is the proof the old mount had no residual users.
Old lease release never runs without `mount_verified=true` and Active
persistence on the new lease.

### C4. `/proc` inspection map (read while frozen; any read error = pinned)

```text
/proc/<pid>/
├── stat        "4321 (bash) T … pgrp …"   membership snapshot (scan /proc/[0-9]+,
│               state T/t = stopped         task must be in frozen holder set; Z excluded)
├── cwd  → /workspace/src                   dentry ref  → cwd_pinned_workspace
├── root → /                                chroot ref  → root_pinned_workspace
├── fd/9 → /workspace/build.log             open file   → fd_pinned_workspace
│    (PTY /dev/pts/*, socket:[…], pipe:[…], anon_inode:[…] are NOT pins)
├── maps  last column = backing path        mmap        → mapped_file_pinned_workspace
└── mountinfo  field 5 = mountpoint         must show the workspace overlay;
    (octal-escaped, e.g. \040 = space)      child mount under workspace blocks;
                                            post-switch: exact lowerdir= list proof
```

Stable block reasons (kept from the historical spec, plus one pre-switch
mechanical reason): `freeze_failed`, `freeze_timeout`,
`process_membership_changed`, `cwd_pinned_workspace`,
`root_pinned_workspace`, `fd_pinned_workspace`,
`mapped_file_pinned_workspace`, `child_mount_pinned_workspace`,
`mountinfo_unavailable`, `mountinfo_mismatch`,
`process_group_unavailable`, `all_task_quiesce_unavailable`, `stage_failed`
(staging mount or staged probe failed before the point of no return),
`unsupported_platform`.

### C5. Failure policy — phase table

| Phase | In-process failure | Boot finds this phase |
| --- | --- | --- |
| before replacement lease | session untouched; report fault if squash itself failed | no remount recovery |
| replacement lease acquired, before first `MS_MOVE` | persist Active(old), release replacement lease, resume tasks, report `leased` + reason | release replacement lease if present; reap/quarantine holder rather than assume tasks resumed |
| after first `MS_MOVE`, before visible verify | persist Faulty; destroy/quarantine; do not release old lease until holder namespace is gone | reap/quarantine holder; keep old lease until namespace death is verified |
| visible verified, before Active persist | persist Faulty; destroy/quarantine; keep both leases until recovery can prove mount state | reap/quarantine holder; release only after namespace death proof |
| Active persisted, before old lease release | release old lease and run refcount GC | release old lease and run refcount GC |
| old lease released, before resume | resume tasks; if resume fails, report faulty and destroy/quarantine | Active state is authoritative; no old-lease recovery |

Faulty reporting is not optional. The result/progress surface must include
workspace session id, attempt generation, phase, upperdir bytes, whether the
upperdir was quarantined or discarded, and lease-release errors. Publishing or
capturing uncertain upperdir state is not allowed.

### C6. Namespace shell runner specifics

- Process-group plumbing already exists end to end and is useful as a seed: the shell runner installs
  every command in its own group via `setpgid(0,0)` before exec
  (`namespace-process/runner/shell_exec.rs`), the PTY records it, and
  `NamespaceExecution::pgid()` exposes it (`namespace-execution/execution.rs`).
  Quiesce still needs holder-scope task discovery; pgid-only freeze is not a
  correctness proof.
- **Interactive PTY bash** (driven via `write_command_stdin` /
  `read_command_lines`) runs with `current_dir` inside the workspace
  (`shell_exec.rs`), so frozen it is always `cwd_pinned_workspace` → always
  "leased". This is physics, not policy: MS_MOVE would leave its cwd dentry on
  the old overlay, and two overlays sharing one upperdir under writes is
  undefined. Such sessions migrate on the first squash run after the shell
  exits, and one-shot sessions reclaim at their finalize-destroy anyway.
- **Batch/waiting commands** (sleeps, network waits, no cwd/fd/mmap/child mount
  under the workspace) can freeze with zero pins → live protocol applies and
  the session migrates without the command ever noticing. Current cwd
  validation keeps absolute cwd inside the workspace; do not use outside-cwd
  examples unless that validation changes.

---

## D. Space complexity — squash with vs. without remount

Notation:

| Symbol | Meaning |
| --- | --- |
| $B$ | base layer bytes |
| $P(t)$ | total bytes of published `L` layers retained in history at time $t$ (grows with publish traffic) |
| $F$ | flatten size — bytes of *surviving* content, $F \le P$; for rewrite-heavy workloads $F \ll P$ |
| $U$ | Σ session upperdir bytes |
| $Q$ | temporary staging bytes and metadata during build/cleanup |
| $\Pi(t)$ | bytes pinned by leases that have **not** migrated (blocked/unswept sessions) |
| $T_{sess}$ | lifetime of the longest-lived session; $T_{sweep}$ = sweep duration (seconds) |

Steady-state disk under $k$ long-lived sessions:

| | no squash | squash **without** remount | squash **with** remount |
| --- | --- | --- | --- |
| after squash commit | — | $B + P + F + U + Q$ (worse than before!) | $B + P + F + U + Q$ (same peak) |
| steady state | $B + P(t) + U$ | $B + P(t) + F + U$ — the whole $P$ stays pinned by live leases | $B + F + U + \Pi(t)$ — only sessions that pass remount shed old pins |
| after sessions end | $B + P + U'$ | $B + F + U'$ | $B + F + U'$ |
| peak duration | — | $O(T_{sess})$ | $O(T_{sweep})$ for clean sessions |
| reclaim latency | session destroy | **max session lifetime** | **seconds** (sweep); blocked: min(shell exit → next squash, destroy) |
| old-session lowerdir chain | $n{+}1$ | $n{+}1$ (unchanged — this is the killer) | clean sessions: $O(\#blocks)$; blocked sessions: unchanged |
| extra I/O per squash | — | $O(F)$ copy | $O(F)$ copy $+ O(k)$ mount syscalls, **0 bytes** for remount itself |

The complexity-class statement:

```text
without remount:  disk = Θ( B + publish history retained by the longest-lived lease + F + U )
with remount:     disk = Θ( B + live content F + U + pins of still-blocked sessions )
```

Squash alone never reclaims under long-lived sessions — it *adds* $F$ and
waits; remount converts reclaim latency from $O(T_{sess})$ to $O(T_{sweep})$
only for sessions that pass the all-task zero-pin proof. Blocked sessions keep
their old lowerdirs. Note the peak is identical in both modes
($B + \Pi + F + U + Q$ at commit); remount changes the peak's **duration**,
not its height.

Worked example (same-file rewrite, the historical perf-report shape): file of
size $s$ rewritten 6 times, one session leased at v6 (pin = L6, blocks =
[L5..L1] → $S_1$, $|S_1| = s$):

```text
                        before      commit       after sweep        after next squash
no squash               B + 6s      —            —                  —        (grows s per rewrite)
squash, no remount      B + 6s      B + 7s       B + 7s  ←stuck     B + 7s   until session destroy → B + 2s
squash + remount        B + 6s      B + 7s       B + 2s  (seconds)  B + s    ([L6,S1] → S2 once L6 unpins)
squash + remount,       B + 6s      B + 7s       B + 7s  (leased)   B + 2s   on first squash after
  cwd-pinned shell                                                           the shell exits
```

Chain-length complexity matters independently of bytes: overlayfs bounds the
lowerdir list by kernel and mount-option limits, and lease-release cleanup cost
scales with chain length. Without remount, a session created at depth $n$
mounts $n{+}1$ lowerdirs forever; with remount each clean session converges to
the rewritten block count. Blocked sessions do not converge until a later
successful sweep or session destroy. If the rewritten chain still exceeds the
measured lowerdir limit, remount blocks with `stage_failed` and leaves the old
lease intact.

---

## Required tests

Unit/integration:

1. `partition_blocks_between_pins_and_base` — incl. singleton runs and
   empty-pin immediate-delete classification.
2. `flatten_masks_whiteouts_opaques_and_shadowed_subtrees` — B-tree of the
   masking rules; whiteouts always retained; source walks no-follow through
   malicious symlinks and opaque dirs.
3. `commit_conflicts_against_racing_squash_and_tolerates_racing_publish`.
4. `ledger_fixpoint_rewrites_old_generation_lease` — B4's ws-2/ws-3 cases,
   incl. skipped substitution whose S layer was reclaimed, missing ledger,
   malformed ledger, and unknown ledger version.
5. `admission_blocks_all_workspace_session_entrypoints` — remount admission
   blocks exec, one-shot create, file read/write/edit, capture, destroy, and
   namespace runner entrypoints.
6. `retarget_never_runs_before_mount_verification` — inject staged/visible
   probe failure; old lease manifest unchanged and replacement lease released.
7. `old_layers_not_deleted_until_refcount_zero` — shared run pinned by a
   second lease survives the first migration.
8. `boot_reconciles_handles_before_sweep` — persisted handles and Pending
   remount records protect layers until recreated or reaped; legacy
   `manager.json` without remount state is treated as Active.
9. `faulty_outcome_is_reported` — post-PONR failure with
   non-empty upperdir reports phase, session id, upperdir bytes, quarantine or
   discard, and lease-release errors.
10. `squash_output_contract` — three-field JSON; `leased` carries the lease
    count and distinct `blocked_reasons`; empty `squashed_blocks` when nothing
    to do; faulty outcomes are visible in the agreed result/progress surface.
11. `ultra_nonfaulty_sweep_converges` — B5 end-to-end: live migration under a
    running command, identity short-circuit, cwd-pinned clean abort,
    `stage_failed` clean abort, zero faulty outcomes, full convergence on the
    following invocation.

Live Docker e2e (required before enabling live remount):

1. `same_upperdir_staged_overlay_kernel_gate` — OLD and NEW overlays coexist
   with the same upperdir/workdir, production-equivalent options, staged
   `MS_MOVE`, visible probe, and rollback unmount.
2. `visible_options_match_production_mount` — helper exposes lowerdir proof
   while matching production `userxattr`/`index` behavior.
3. `all_task_quiesce_blocks_escaped_pgid_child` — child changes pgid/session
   while sharing holder namespace; remount blocks unless the all-task proof
   stops it.
4. `hidden_masks_not_visible_during_remount` — daemon paths are not observable
   by any live task during unmask/remask.
5. `proc_pin_matrix_blocks_uncertainty` — cwd, root, fd, mmap, child mount,
   mountinfo escaping, permission failure, and process churn each block with a
   stable reason.
6. `crash_matrix_recovery` — crash at every C5 phase; restart never sweeps a
   layer before handle/Pending reconciliation.
7. `lowerdir_limit_blocks_cleanly` — rewritten lowerdir list above the measured
   limit returns `stage_failed`, releases replacement lease, and leaves the old
   lease intact.
