# Per-Call Snapshot Layer Stack - Gap Analysis

## Reviewed Files

- `.omc/plans/per-call-snapshot-layer-stack-diagrams.md`
- `.omc/plans/per-call-snapshot-layer-stack-implementation.md`
- `.omc/plans/per-call-snapshot-layer-stack-simplified-implementation.md`

## Summary

The original diagrams and implementation plan contain the right core idea:
snapshot manifest, per-call overlay upperdir, OCC-gated merge, append-only
layers, leases, and squash. The main gaps are that several sections still
describe an older design shape:

1. `overlay/` owns too much durable state.
2. OCC has an overlay-specific in-namespace entrypoint.
3. final validation and publish are not explicitly atomic.
4. the diagrams still show dropped shell modes and coalescing.
5. the tracked-file hash policy is not stated as a durable contract.
6. critical algorithms are described across several sections instead of as
   standalone implementation contracts.

The simplified design should be the target shape:

```text
layer_stack = append-only workspace state
overlay     = per-call mount and upperdir capture
occ         = typed changeset policy and commit transaction
runtime     = sequencer between overlay, OCC, and layer_stack
```

## Critical Gaps

| Gap | Where it appears | Problem | Correct target |
|---|---|---|---|
| Dropped shell modes still appear in diagrams | `per-call-snapshot-layer-stack-diagrams.md`, diagram 3 | It shows `read_only`, `gated`, `strict_stale`, `exclusive`, and strict staleness rejection even though the implementation plan says modes are gone. | Replace with empty-upperdir fast path, automatic OCC routing, and informational-only staleness. |
| Coalescing appears as part of layer creation | `per-call-snapshot-layer-stack-diagrams.md`, diagram 3 | The implementation plan defers cross-request coalescing, so the diagram overstates shipped behavior. | Per changeset: zero or one layer. Coalescing is a future optimization. |
| Durable layer stack is under `sandbox/overlay/` | `per-call-snapshot-layer-stack-implementation.md`, components summary and steps 0-4 | Readers cannot tell whether overlay owns durable workspace state or just per-call filesystem execution. | Move durable state to `sandbox/layer_stack/`. Keep `sandbox/overlay/` for mount/capture only. |
| OCC has `occ/runtime/apply.py:apply_inproc` that accepts upper changes | `per-call-snapshot-layer-stack-implementation.md`, ADR-4 and Step 7a | OCC becomes conceptually overlay-aware. The user corrected that OCC should not do `apply_overlay_capture`. | `runtime/overlay_shell/capture_to_changeset.py` converts upper changes to typed OCC changes, then calls `occ.client.OCCClient.apply_changeset`. The internal service accepts typed changes only behind that client boundary. |
| `shell_pipeline` naming remains generic | `per-call-snapshot-layer-stack-implementation.md`, Step 8 | The shell projection path is hidden inside generic runtime naming. | Use `runtime/overlay_shell/pipeline.py`. |
| `LayerManager.commit` is used for final publish | `per-call-snapshot-layer-stack-implementation.md`, Step 4, Step 7, ADR summary | The name hides policy boundaries and final validation. | `OccCommitTransaction.revalidate_and_publish` calls `LayerPublisher.publish_layer` under a layer-stack transaction. |
| `intent.py` and `layer_backed_content.py` are vague names | simplified target structure draft | `intent` does not say what was prepared, and `layer_backed_content` combines reads and staged writes behind an adapter name. | Use `changeset/prepared.py`; put storage reads and layer deltas in `layer_stack.merged_view` and `layer_stack.changes`. |
| `orchestrator.py`, `direct/`, and `gated/` make OCC hard to scan | simplified target structure draft and live code names | `orchestrator` is too broad, and `gated` describes a mechanism rather than the path policy. | Use grouped workflow modules: `routing/router.py`, `routing/gitignore.py`, `merge/transaction.py`, `merge/tracked.py`, and `merge/direct.py`. |
| Final revalidation at publish time is missing | `per-call-snapshot-layer-stack-implementation.md`, Step 4 and ADR rev 4 | Two requests can both validate against manifest M and then publish stale overlapping tracked writes. | OCC may prepare concurrently, but active-manifest revalidation plus layer publish must be atomic under the layer-stack transaction lock. |
| "No additional global lock needed" conflicts with the correction | `per-call-snapshot-layer-stack-implementation.md`, changelog rev 4 | Layer atomicity alone does not protect tracked-path OCC semantics across concurrent requests. | Use a short global active-manifest transaction, not a global OCC lock around shell/capture/prepare. |
| Tracked `base_hash` source is not explicit | `per-call-snapshot-layer-stack-implementation.md`, Step 4 and Step 7 | If `base_hash` can be derived from the active manifest during prepare, a long-running request can lose the snapshot it actually edited. | `base_hash` comes from the request's leased snapshot manifest. The active manifest is read only inside `OccCommitTransaction` for final comparison. |
| Shell-captured tracked writes look mergeable | `per-call-snapshot-layer-stack-implementation.md`, Step 7 and shell result flow | A shell upperdir contains final full-file bytes, not edit anchors. Treating shell output like anchor edits would hide real concurrent rewrites. | Shell-captured tracked writes are strict full-file CAS writes with `base_hash`; any tracked conflict rejects the whole shell request layer. |
| Shell direct outputs can publish after tracked conflict | old shell/OCC flow descriptions | If tracked shell writes conflict but gitignored/direct outputs still publish, one command can partially leak side effects even though its code edit failed. | Direct/gitignored files captured by the same shell request are held until tracked validation passes; if any tracked shell path conflicts, publish no layer. |
| Squash/hash interaction is underspecified | squash sections in old docs | A squash that removes old layer bytes needed by an active lease can make OCC infer base hashes from the wrong content or from nothing. | Squash may replace old layers in the active manifest, but leased snapshot data must remain readable until lease release. Missing leased snapshot data fails closed. |
| Direct merge prefix policy is underspecified | `per-call-snapshot-layer-stack-implementation.md`, Step 4 | The plan adds `DirectMergePolicy` but does not isolate the algorithm. | Define direct path handling in the OCC commit transaction algorithm. |
| Lease budget is only briefly described | `per-call-snapshot-layer-stack-implementation.md`, Step 9 | Kill/backpressure/evict behavior is not a standalone algorithm. | Add a lease-budget algorithm file with deterministic decisions and enforcement points. |
| Squash algorithm is in diagrams but not separated as an implementation contract | both old docs | It is hard to tell what must be CAS-checked, what is lease-aware, and what never calls OCC. | Add a layer-publish-and-squash algorithm file. |
| Crash recovery/fsck is risk-level only | `per-call-snapshot-layer-stack-implementation.md`, risks | Orphan staging dirs, orphan layer dirs, retired layers, and manifest consistency need a clearer maintenance algorithm. | Include recovery and GC invariants in the layer-publish/squash and lease-budget docs. |
| Generic `wire.py` appears in the target tree | simplified target structure draft | It hides what is actually serialized and creates another vague bucket. | Drop `wire.py`; put serialization beside concrete boundaries such as `manifest.py`, `result_envelope.py`, `client.py`, and `runtime_ops.py`. |
| `overlay/capture/ndjson.py` appears as a primary capture contract | simplified target structure draft | The new runtime converts capture to typed changes in-process, so NDJSON should not be a core module. | Drop it from the target tree; keep optional debug dumps behind `result_envelope.py` or a diagnostic helper only if needed. |

## Critical Algorithm Documents Added

- `.omc/plans/per-call-snapshot-layer-stack-algorithm-snapshot-and-lease.md`
- `.omc/plans/per-call-snapshot-layer-stack-algorithm-merged-view.md`
- `.omc/plans/per-call-snapshot-layer-stack-algorithm-overlay-shell-runtime.md`
- `.omc/plans/per-call-snapshot-layer-stack-algorithm-occ-commit-transaction.md`
- `.omc/plans/per-call-snapshot-layer-stack-algorithm-layer-publish-and-squash.md`
- `.omc/plans/per-call-snapshot-layer-stack-algorithm-lease-budget-and-gc.md`

## Recommended Follow-Up Edits To Existing Docs

Do not keep three competing descriptions indefinitely. After the algorithm
files are reviewed:

1. Update `per-call-snapshot-layer-stack-diagrams.md` diagram 3 to remove modes,
   strict-stale rejection, exclusive locking, and coalescing-as-current-path.
2. Replace the old `sandbox/overlay/layer_manager.py` target with
   `sandbox/layer_stack/stack_manager.py`.
3. Replace `sandbox/occ/runtime/apply.py` with `occ/service.py` plus
   `runtime/overlay_shell/capture_to_changeset.py`.
4. Replace `LayerManager.commit(staged_changes)` with
   `OccCommitTransaction.revalidate_and_publish(...)` and
   `LayerPublisher.publish_layer(...)`.
5. Update ADR-4 so the runtime sequences overlay capture and OCC service, but
   OCC does not expose an overlay-specific API.
6. Add a new ADR for the commit transaction:
   "OCC preparation is concurrent; final active-manifest revalidation and
   publish are serialized."
7. State the hash policy explicitly: tracked `base_hash` is computed from, or
   inferred from, the request's leased snapshot manifest, never from the active
   manifest during prepare.
8. Require shell-to-OCC changesets to carry the leased snapshot identity so OCC
   can infer base hashes after shell command completion.
9. State that shell-captured tracked writes are full-file CAS writes. They do
   not use anchor merge, and any tracked conflict rejects the whole shell layer.
10. State that shell-captured direct/gitignored outputs publish only if tracked
    shell validation succeeds for the same request.
11. State that squash and GC preserve leased snapshot readability until lease
    release; missing leased snapshot data is a fail-closed storage invariant
    violation, never a fallback to active content.
12. Drop generic `wire.py` modules from the target structure. Use object-local
    serialization helpers or boundary-local request/response shaping instead.
13. Drop `overlay/capture/ndjson.py` from the primary structure. The core path
   should be in-process capture objects -> typed OCC changes -> result envelope.

## Acceptance Bar For The Revised Plan

The design is coherent when these statements are all true:

1. `layer_stack` never imports `overlay`, `occ`, or `git`.
2. `overlay` never imports `occ` or `git`.
3. `occ` never names overlay capture as an operation.
4. `runtime/overlay_shell` is the only bridge from upperdir capture to typed
   OCC changes.
5. Every write/edit/shell mutation enters through `OCCClient.apply_changeset`;
   `OCCClient` delegates to `OccService.apply_changeset` internally.
6. Every accepted mutation publishes through `OccCommitTransaction`.
7. `OccCommitTransaction` revalidates tracked paths against the active manifest
   while holding the layer-stack transaction lock.
8. A request publishes at most one layer for accepted changes.
9. Squash uses the same manifest-CAS primitive as normal publish and never
   deletes data still needed by leased snapshots.
10. Staleness is telemetry only; it is not a rejection path.
11. Every tracked write/delete prepared for OCC carries a `base_hash` from the
    leased snapshot manifest or is a create-only operation.
12. Active-manifest hashes are read for tracked validation only inside
    `OccCommitTransaction`.
13. Shell-captured tracked writes are strict full-file CAS writes, not
    mergeable anchor edits.
14. If any shell-captured tracked file conflicts, the shell request publishes no
    layer, including any direct/gitignored outputs from that same shell command.
15. Missing leased snapshot content fails closed and never falls back to active
    manifest content.
