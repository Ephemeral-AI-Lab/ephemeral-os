# Rust parity audit — Squash algorithm (depth limit, segment around lease heads, deferred GC)

Domain: sandbox. Audited Rust against Python ground truth + architecture docs.

Verdict: the **core squash algorithm** (`eos-layerstack/src/squash.rs` + `stack.rs::squash` + `lease.rs`) is a **faithful, operator-for-operator port**. All five checklist invariants are genuine `match`es with bilateral evidence. The disparities are NOT in the core algorithm — they live at the trigger/wiring/audit layer: (a) a second Python squash trigger (shell pre-mount, env-configurable depth 64) is absent in Rust; (b) the `eos-occ::AutoSquashMaintenancePolicy` is dead/mirror code with a doc comment describing serialization + re-read it does not implement; (c) the auto-squash audit emission is reduced to one event with a different `squash_trigger_reason`; (d) `_remove_layers` drops the `evict_layer_index` call (intentionally benign — Rust `MergedView` is stateless).

---

## Ground truth

Python (authoritative for dynamics/constants/ordering):
- `/tmp/oldpy/backend/src/sandbox/layer_stack/squash.py` — `LayerCheckpointSquasher.plan` (61-93), `_segment_around_lease_heads` (142-164), `build_checkpoint` (95-113), `relabel_checkpoint` (115-126), `manifest_prefix_before_plan` (167-176), `_default_checkpoint_id` (179-180).
- `/tmp/oldpy/backend/src/sandbox/layer_stack/stack.py` — `squash` (236-298), `release_lease` (137-149), `_unreferenced_layers` (375-382), `_remove_layers` (384-388), `can_squash` (157-168).
- `/tmp/oldpy/backend/src/sandbox/layer_stack/lease.py` — `leased_layers` (57-66) vs `lease_head_layers` (68-85) DUAL-SET.
- `/tmp/oldpy/backend/src/sandbox/layer_stack/manifest.py:88-89` — `Manifest.depth == len(layers)`.

Trigger (the depth limit lives at the caller, NOT in `squash.py`):
- `/tmp/oldpy/backend/src/sandbox/occ/service.py:34` — `AUTO_SQUASH_MAX_DEPTH = 100` (hardcoded module constant).
- `/tmp/oldpy/backend/src/sandbox/occ/maintenance.py:29-95` — `AutoSquashMaintenancePolicy.after_publish_sync`: depth gate `active.depth <= max_depth` (50), then under `self._squash_lock` (44, 56) RE-READS active (57), RE-CHECKS depth (58), `can_squash` (60), emits audit (63/79/89), squashes (70).
- `/tmp/oldpy/backend/src/sandbox/daemon/occ_runtime_services.py:62-67` — wires the policy with `max_depth=AUTO_SQUASH_MAX_DEPTH` and `audit=emit_squash_event`.
- `/tmp/oldpy/backend/src/sandbox/ephemeral_workspace/pipeline.py:243-273, 455-462` — SECOND trigger `_run_shell_pre_mount_maintenance`, depth `EOS_SHELL_MOUNT_SQUASH_MAX_DEPTH` default **64**, env-configurable, `<= 0` disables.
- `/tmp/oldpy/backend/src/sandbox/daemon/layer_stack_runtime.py:248-290` — `emit_squash_event` emits `layer_stack.{squash_triggered, squash_completed, squash_failed}`.

Docs: `docs/architecture/sandbox/layerstack.html` §2.5 (lines 219-317) + `.omc/wiki/layerstack-squash-workflow-and-deferred-gc.md` corroborate the algorithm exactly (run = contiguous non-head layers; barriers = `lease_head_layers`; `min_reduction` default 1, `can_squash` uses 2; deferred GC at lease release).

### Constant reality (user said "~100")
- Auto-squash depth limit = **100, hardcoded** on BOTH sides (`occ/service.py:34`, `eos-occ/src/service.rs:19` AND `eos-layerstack/src/lib.rs:66`). The "~100" belief is correct for auto-squash.
- Three-way nuance: Python ALSO has a **second, env-configurable** depth limit **64** (`EOS_SHELL_MOUNT_SQUASH_MAX_DEPTH`) on the shell kernel-mount pre-squash path. Rust has no such path/trigger → that configurable limit is absent (see Disparity D1).
- `max_depth`/`min_reduction` are plain parameters to `plan`/`squash`; not otherwise configurable.

---

## Rust mapping

| Python | Rust |
|---|---|
| `squash.py` `LayerCheckpointSquasher.plan` (61-93) | `eos-layerstack/src/squash.rs:160-209` `plan` |
| `_segment_around_lease_heads` (142-164) | `squash.rs:366-397` `segment_around_lease_heads` + `flush_run` |
| `build_checkpoint` (95-113) | `squash.rs:221-249` |
| `relabel_checkpoint` (115-126) | `squash.rs:260-289` (adds explicit `fsync_dir` of parent, matching py `fsync_path` line 125) |
| `manifest_prefix_before_plan` (167-176) | `squash.rs:351-364` |
| `_default_checkpoint_id` (179-180) `B{v:06d}-{uuid8}` | `squash.rs:28,316` `B{v:06}-{unique:08x}` (process counter, not UUID — documented 26-27) |
| `stack.py squash` (236-298) | `stack.rs:414-482` |
| `stack.py release_lease/_unreferenced_layers/_remove_layers` (137-149,375-388) | `stack.rs:383-387, 810-848` |
| `lease.py leased_layers/lease_head_layers` (57-85) | `lease.rs:138-154` |
| `occ/service.py:34 AUTO_SQUASH_MAX_DEPTH=100` | `eos-occ/src/service.rs:19` + `eos-layerstack/src/lib.rs:66` |
| `occ/maintenance.py AutoSquashMaintenancePolicy` (29-95) | `eos-occ/src/service.rs:93-117` (DEAD — not instantiated; see D2) AND functionally re-implemented at `eos-daemon/src/dispatcher.rs:1622-1684 run_auto_squash_maintenance` (the live trigger) |
| `pipeline.py _run_shell_pre_mount_maintenance` (243-273) | ABSENT (D1) |
| `layer_stack_runtime.py emit_squash_event` 3 events (248-290) | `dispatcher.rs:3286-3307` (1 event, different reason — D3) |

---

## Invariant table

| # | Invariant | Status | Sev | Python file:line | Rust file:line | Note |
|---|---|---|---|---|---|---|
| 1 | Squash TRIGGERED at a depth limit; real constant + hardcoded/configurable | match | none | `occ/service.py:34` `AUTO_SQUASH_MAX_DEPTH = 100`; gate `squash.py:73` `active_manifest.depth <= max_depth` (`<=`) | `eos-layerstack/src/lib.rs:66` & `eos-occ/src/service.rs:19` `= 100`; gate `squash.rs:177` `layers.len() <= max_depth` (`<=`) | Limit is **100, hardcoded** both sides. `Manifest.depth==len(layers)` (`manifest.py:88` / `cas.rs:136`). Live trigger = `dispatcher.rs:1633`. (2nd configurable limit 64 → D1.) |
| 2 | Merge consecutive non-leased runs into checkpoint segments, segment AROUND lease heads | match | none | `_segment_around_lease_heads` `squash.py:142-164`: flush run at each head, `len(run)>1` → segment | `segment_around_lease_heads`/`flush_run` `squash.rs:366-397`: `run.len()` `_ =>` Segment | Logic identical: head → `flush_run` + `Keep`; else accumulate. `flush_run` 0/1/≥2 buckets match exactly (py 150-155 / rs 388-393). |
| 3 | Lease-HEAD layers NOT folded during squash; squashed only after lease releases (deferred GC) | match | none | heads kept as passthrough `stack.py:285-286`; GC only in `release_lease`→`_remove_layers` `stack.py:137-149,384-388` | heads → `SquashPlanEntry::Keep`→`new_layers.push` `stack.rs:449`; GC only in `release_lease_locked`→`remove_layers` `stack.rs:383-387,810-848` | Squash never deletes; head stays in active. Confirmed by test `release_lease_gcs_squashed_layers_after_retaining_lease_drops` `stack.rs:1302-1325`. |
| 4 | Guards: `max_depth` + `min_reduction` gate whether squash runs | match | none | `squash.py:73`(`<=`),`:77`(`>=`),`:79`(`<`),`:84-86`(`>`+`<=`); defaults `min_reduction=1` (68), `can_squash` passes 2 (`stack.py:166`) | `squash.rs:177`(`<=`),`:182`(`>=`),`:185`(`<`),`:195-199`(`>`+`<=`); `squash` passes 1 (`stack.rs:422`), `can_squash` passes 2 (`stack.rs:401`) | All four guards present, operator-for-operator identical. Guard ORDER load-bearing in Rust: `:182 >=` early-return prevents `usize` underflow at `:185` (Python uses signed int, no underflow risk). |
| 5 | Non-destructive until retaining lease releases: pointer-swap shorter manifest, lower layers stay on disk until GC | match | none | manifest swap `write_manifest_atomic` `stack.py:291`; synthetic squash lease pins `:247-250`; GC deferred `:137-149`; skip-set = active ∪ leased `:381` | `write_manifest` `stack.rs:464`; synthetic lease `stack.rs:425-431`; GC deferred `stack.rs:383-387`; skip-set `unreferenced_layers` `stack.rs:824-835` `!active.contains && !retained.contains` | Confirmed by `stack.rs:1316-1318` (old tail exists post-squash) then `:1321-1323` (gone post-release). Cross-instance variant `:1327-1362`. |

All five = `match` with quoted Rust anchors (never asserted from absence).

---

## Disparities

### D1 — Shell pre-mount squash trigger (env-configurable depth 64) is ABSENT in Rust — missing/divergent
- Python: `ephemeral_workspace/pipeline.py:243-273` `_run_shell_pre_mount_maintenance` squashes before a shell enters the kernel overlay mount, with `max_depth = _shell_mount_squash_max_depth()` (`:455-462`) = `EOS_SHELL_MOUNT_SQUASH_MAX_DEPTH` env var, **default 64**, `max(0,int)`, `<=0` disables. Emits `layer_stack.shell_pre_mount_squash.*` timings.
- Rust: no such hook. `grep` over `eos-daemon`, `eos-overlay`, `eos-runner` finds no `pre_mount`/`shell_mount`/`SHELL_MOUNT_SQUASH`. The `0..64` at `eos-overlay/src/kernel_mount.rs:77` is an unrelated retry loop, not a depth cap. The Rust kernel mount is the **modern `fsconfig`/`lowerdir+` API** (`kernel_mount.rs:93-113`), which is NOT subject to the util-linux `mount(8)` ~16-layer limit the doc (`layerstack.html:167-168`) cites as one motivation — so the practical need for the 64-cap pre-squash is genuinely reduced.
- Severity: **low** (leaning intentional migration deferral). Why it matters: the *auto-squash-after-publish* path (depth 100) still bounds depth, so a deep manifest is eventually compacted; but Python proactively compacts to ≤64 right before a shell mount to cut MergedView read-amplification and stay clear of mount-layer limits. Rust shells may mount deeper stacks and pay more per-read cost, and the `EOS_SHELL_MOUNT_SQUASH_MAX_DEPTH` operational knob is gone.
- Suggested fix: confirm with owners whether the pre-mount squash is intentionally deferred (modern mount API + 100-cap auto-squash deemed sufficient). If not, add a pre-mount maintenance hook in the daemon overlay-mount path calling `stack.squash(64)` (env-overridable) and emit the matching timings.

### D2 — `eos-occ::AutoSquashMaintenancePolicy` is dead/mirror code; its doc comment over-claims behavior it does not implement — divergent/bug-of-doc
- Rust: `eos-occ/src/service.rs:93-117`. Struct holds ONLY `{ squasher, max_depth }` — **no `_squash_lock` field**. `after_publish_sync` (`:111-116`) is a one-liner: `if published && can_squash { squash }`. No under-lock re-read of active, no depth pre-check, no audit emission. The doc comment `:88-91` claims: *"Each policy owns its own squash lock (Python `_squash_lock`) so concurrent publishes do not double-squash; it re-reads the active manifest under the lock before deciding."* None of that is in the code. The struct is exported (`lib.rs:35`) but **never instantiated** in production — `grep` finds only the def + unit tests, no `AutoSquashMaintenancePolicy::new` call site outside tests. The LIVE trigger is `dispatcher.rs:1622 run_auto_squash_maintenance`.
- Python: `occ/maintenance.py:44-95` genuinely takes `self._squash_lock` (`:56`), re-reads + re-checks depth under it (`:57-58`), and emits audit.
- Severity: **low** (the dead policy doesn't run, so no runtime divergence today). Why it matters: a future reader wiring this policy in (it's the natural OCC port surface) inherits a comment that lies about serialization + idempotency re-checks, risking a double-squash / TOCTOU regression. The live `run_auto_squash_maintenance` ALSO lacks the policy-local lock and under-lock re-read (it reads active once at `dispatcher.rs:1625`, then `can_squash`+`squash` re-take the writer lock internally — acceptable since `LayerStack::squash` re-plans under the storage-writer guard and `manifest_prefix_before_plan` is OCC-safe), so correctness holds, but the Python `_squash_lock` "at most one squash worker" property is not explicitly reproduced.
- Suggested fix: either delete `eos-occ::AutoSquashMaintenancePolicy` (+ trait) as unused, or fix the doc comment to match the actual no-lock/no-re-read body, and confirm the writer-lock serialization in `run_auto_squash_maintenance` is the intended replacement for `_squash_lock`.

### D3 — Auto-squash audit emission reduced to one event with a different trigger reason — divergent
- Python: `daemon/layer_stack_runtime.py:248-290` + `occ/maintenance.py:63-93` emit THREE events — `layer_stack.squash_triggered` (`squash_trigger_reason="post_publish_depth"`, input depth), `layer_stack.squash_completed` (input/result depth + manifest root hash), `layer_stack.squash_failed` (`failure_kind="raced_or_plan_aborted"`).
- Rust: `eos-daemon/src/dispatcher.rs:3286-3307 emit_layer_stack_maintenance_audit` emits ONLY `layer_stack.squash_completed`, only when `auto_squash.applied > 0`, with `squash_trigger_reason="auto_squash"` (NOT `post_publish_depth`), no manifest root hash, and no `squash_triggered`/`squash_failed` events. The protocol field exists (`eos-protocol/src/audit.rs:89 squash_trigger_reason`) but the value diverges.
- Severity: **medium** (observability/audit-stream parity, not algorithm). Why it matters: `layerstack.html:317` and the public edge-case suite assert these three events + the `post_publish_depth` reason. Audit consumers / e2e assertions keyed on `squash_triggered`, `squash_failed`, or `squash_trigger_reason="post_publish_depth"` will not see them. Raced/aborted squashes are silent in the Rust audit stream.
- Suggested fix: emit `squash_triggered` (reason `post_publish_depth`) before the squash and `squash_failed` on the raced/None path; align `squash_completed`'s reason value and add the manifest root hash field.

### D4 — `_remove_layers` drops the `evict_layer_index` call — divergent (intentional, benign)
- Python: `stack.py:384-388 _remove_layers` does THREE ops per GC'd layer: `remove_path`, `layer_digest_path(...).unlink(missing_ok=True)`, and `self._view.evict_layer_index(layer.layer_id)`.
- Rust: `stack.rs:837-848 remove_layers` does TWO: `remove_path` + remove digest file. No index eviction.
- Severity: **low** — intentional and benign. Why: Rust `MergedView` is **stateless** (`stack.rs:69-72 struct MergedView { storage_root }` only; every read hits disk, `read_bytes` `:90-122`). There is no per-layer index cache to invalidate, so the Python `evict_layer_index` has no Rust counterpart by design. No bug, but surfaced per audit policy (dropped call must be noted even when benign).
- Suggested fix: none; optionally a one-line comment in `remove_layers` noting the stateless-view rationale.

---

## Extra findings

- **Guard-order correctness (Rust-specific, positive).** `squash.rs:182` `if entries.len() >= active_manifest.layers.len() { return Ok(None) }` MUST precede `:185` `layers.len() - entries.len() < min_reduction`, because the subtraction is `usize` and would panic on underflow. Python (`squash.py:77` then `:79`) uses signed ints so order is not load-bearing there. The Rust order is correct; flagging because reordering these two lines would introduce a panic.
- **`build_checkpoint` rollback parity.** Both discard staging on projection/rename failure (py `squash.py:110-112` `shutil.rmtree` in `except`; rs `squash.rs:234-244` `remove_dir_all` on each error). Match.
- **`relabel_checkpoint` durability.** Rust adds an explicit `fsync_dir(parent)` (`squash.rs:282-284`) mirroring Python's `fsync_path(layer_dir.parent)` (`squash.py:125`); documented inline. Match (Rust slightly more explicit). Note Rust also `create_dir_all(parent)` (`:274-276`) which Python relies on existing — harmless.
- **Squash error/rollback aggregation.** Rust `stack.rs:469-481`: on non-commit, discards all built checkpoints, then ALWAYS releases the synthetic squash lease, and folds `(outcome, release)` so a release error surfaces. Matches Python `finally` (`stack.py:294-298`) which discards uncommitted checkpoints and releases the lease. The synthetic-lease-pins-against-GC-race invariant (py `:247-250`, doc `:280`) is preserved (rs `:425-431`).
- **`CHECKPOINT_ID_PREFIX = 'B'`** (`squash.rs:28`) matches Python `B{...}` (`squash.py:180`); relabel prefix check `format!("B{next_version:06}-")` (`stack.rs:452`) matches py `f"B{next_version:06d}-"` (`stack.py:274`). Match.
- **`can_squash` min_reduction=2 vs squash min_reduction=1** preserved on both sides (`stack.py:166` vs default-1; `stack.rs:401` vs `stack.rs:422`). This asymmetry (can_squash is stricter) is intentional and ported.
- **Lease DUAL-SET** (`leased_layers` full retention vs `lease_head_layers` squash barriers) faithfully ported including the sort/dedup: Python `tuple(sorted({...}))` (`lease.py:77-84`) → Rust `BTreeSet<LayerRefKey>` (`lease.rs:145-154`). Match.

---

## Open questions

1. Is the shell pre-mount squash (D1, env `EOS_SHELL_MOUNT_SQUASH_MAX_DEPTH`=64) an INTENTIONAL migration deferral (justified by the modern `fsconfig`/`lowerdir+` mount API removing the ~16-layer `mount(8)` cap) or a genuinely dropped dynamic? The 100-cap auto-squash still bounds depth, but the proactive ≤64 pre-mount compaction and its operator knob are gone.
2. Is `eos-occ::AutoSquashMaintenancePolicy` (D2) intended to ever be wired, or should it be deleted? If kept, its `_squash_lock`/re-read doc comment must be corrected or the behavior implemented.
3. Does the daemon's `run_auto_squash_maintenance` need the Python `_squash_lock` "at most one squash worker" serialization, or is the per-root storage-writer lock + OCC `manifest_prefix_before_plan` re-plan considered an equivalent guarantee? (Believed equivalent for correctness; not byte-identical to the Python lock model.)
4. Should the Rust audit stream (D3) restore `squash_triggered`/`squash_failed` and the `post_publish_depth` reason for parity with the documented critical-lane audit contract and e2e assertions?
