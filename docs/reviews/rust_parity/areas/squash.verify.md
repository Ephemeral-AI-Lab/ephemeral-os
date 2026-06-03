# Independent Verification — Squash algorithm (depth limit, segment around lease heads, deferred GC)

Area key: `sandbox/squash`
Verifier posture: trust nothing, re-derived from primary sources.

Primary sources opened:
- Python: `/tmp/oldpy/backend/src/sandbox/layer_stack/squash.py`, `stack.py`, `lease.py`,
  `occ/service.py`, `occ/maintenance.py`, `ephemeral_workspace/pipeline.py`,
  `daemon/layer_stack_runtime.py`, `audit/schema.py`, `layer_stack/manifest.py`.
- Rust: `sandbox/crates/eos-layerstack/src/squash.rs`, `stack.rs`, `lease.rs`, `lib.rs`;
  `eos-occ/src/service.rs`; `eos-daemon/src/dispatcher.rs`;
  `eos-protocol/src/cas.rs`; `eos-overlay/src/kernel_mount.rs`.

## Invariant verdict table

| # | Invariant | Independent status | Decisive bilateral anchor |
|---|-----------|--------------------|---------------------------|
| 1 | Squash TRIGGERED at a depth limit; real constant + hardcoded/configurable | confirmed_match (with nuance) | py `occ/service.py:34 AUTO_SQUASH_MAX_DEPTH = 100`; live gate `maintenance.py:50 active.depth <= self._max_depth` ⇒ skip. rs live path `dispatcher.rs:1633 if active.depth() <= AUTO_SQUASH_MAX_DEPTH` (imported from `eos_layerstack`, `lib.rs:66 pub const AUTO_SQUASH_MAX_DEPTH: usize = 100`). `Manifest.depth==len(layers)` py `manifest.py:88-89` = rs `cas.rs:136-137`. **Constant is 100, hardcoded** (not 100 the user "thinks"). |
| 2 | Merge consecutive non-leased runs into checkpoint segments, segmenting AROUND lease heads | confirmed_match | py `_segment_around_lease_heads squash.py:142-164` (head→flush_run+append(layer); flush_run buckets len 0 / 1→keep / >1→CheckpointSegment). rs `segment_around_lease_heads squash.rs:366-382` + `flush_run squash.rs:384-397` (match 0 / 1→Keep / `_`→Segment). Identical structure. |
| 3 | Lease-HEAD layers NOT folded; squashed only AFTER lease releases (deferred GC) | confirmed_match | py head kept as passthrough `stack.py:285-286`; GC only via `release_lease→_remove_layers stack.py:137-149,384-388`. rs head→`SquashPlanEntry::Keep`→`new_layers.push stack.rs:449`; GC only via `release_lease_locked→remove_layers stack.rs:383-387,810-848`. Proven by rs tests `stack.rs:1302-1325` (release_lease_gcs_squashed_layers_after_retaining_lease_drops) + `1327-1362` (cross-instance). |
| 4 | Guards preserved: max_depth + min_reduction gate squash | confirmed_match | py `squash.py:73(<=),77(>=),79(<),84-86(>+all <=)`; default `min_reduction=1` (sig line 67); `squash()` omits arg ⇒ 1; `can_squash` passes 2 (`stack.py:166`). rs `squash.rs:177(<=),182(>=),185(<),195-198(>+all <=)`; `squash` passes 1 (`stack.rs:422`), `can_squash` passes 2 (`stack.rs:401`). Operators + thresholds match exactly. |
| 5 | Non-destructive until retaining lease releases: pointer-swap shorter manifest, lower layers stay on disk until GC | confirmed_match | py `write_manifest_atomic stack.py:291`; synthetic squash lease pin `stack.py:247-250`; GC deferred via release; skip-set `active ∪ leased stack.py:381`. rs `write_manifest stack.rs:464`; synthetic lease `stack.rs:425-431`; `unreferenced_layers = !active.contains && !retained.contains stack.rs:824-835`. Proven on disk by rs test `stack.rs:1316-1323` (layers still exist post-squash, gone post-release). |

All five invariants: **confirmed_match.** No FALSE MATCH detected — the core squash
algorithm (trigger constant, segmentation around lease heads, deferred-GC of folded
non-head layers, dual-guard gating, pointer-swap non-destructiveness) is faithfully
ported. The Rust even carries the DUAL-SET (`leased_layers` retention vs
`lease_head_layers` barrier) distinction explicitly (`lease.rs:1-13,135-154`,
mirroring `lease.py:57-85`).

### Nuance on invariant 1 (reported, not a defect)
There are TWO `AUTO_SQUASH_MAX_DEPTH = 100` definitions in Rust:
`eos-occ/src/service.rs:19 (u32)` and `eos-layerstack/src/lib.rs:66 (usize)`. The LIVE
trigger (`dispatcher.rs:33` import, used at `1633/1635/1647`) uses the **usize** one.
The `u32` copy in eos-occ feeds only the dead `AutoSquashMaintenancePolicy` (see D2).
Both equal 100; no value divergence. Type duplication is cosmetic, not behavioral.

## Disparity adjudication

| ID | Investigator claim | Adjudication | Reasoning |
|----|--------------------|--------------|-----------|
| D1 | Shell pre-mount squash trigger (env-configurable depth 64) absent in Rust | **confirmed** (severity low) | Python has a genuine SECOND squash entry point: `pipeline.py:243-273 _run_shell_pre_mount_maintenance` ("Collapse deep manifests before shell enters the kernel mount path"), gated by `_shell_mount_squash_max_depth() pipeline.py:455-462` reading env `EOS_SHELL_MOUNT_SQUASH_MAX_DEPTH` default **64**. Rust grep over all sandbox crates for `shell_pre_mount`/`SHELL_MOUNT_SQUASH`/`run_shell_pre_mount` → **0 hits**. The only live Rust squash trigger is post-publish `run_auto_squash_maintenance` (depth 100). The `0..64` at `kernel_mount.rs:76` is genuinely an UNRELATED umount-retry loop (PORT comment: "umount loop on teardown", py `kernel_mount.py:78-121`), not a depth cap. Investigator's read is exactly right. Low severity: a perf/preventive collapse, not a correctness invariant — the post-publish trigger still bounds depth at 100. |
| D2 | `eos-occ::AutoSquashMaintenancePolicy` is dead/mirror code; doc over-claims a `_squash_lock` + under-lock re-read it does not implement | **confirmed** (severity low) | `service.rs:93-117` is the policy struct + `after_publish_sync`, with NO squash lock, NO under-lock re-read, NO audit emit — yet the doc comment (`service.rs:89-91`) claims "Each policy owns its own squash lock (Python `_squash_lock`)... re-reads the active manifest under the lock". Python `maintenance.py:44 self._squash_lock`, `:56-58` re-reads under lock. grep for `AutoSquashMaintenancePolicy` outside its own module → only the `lib.rs:35` re-export; no construction site. Live path is `dispatcher.rs:1622-1684`. So it is unreachable mirror code with a doc that over-claims. Confirmed. |
| D3 | Auto-squash audit reduced to one event with a divergent trigger reason | **adjusted** (severity medium→stands at medium) | Substance confirmed AND understated. Python `layer_stack_runtime.py:248-287 emit_squash_event` emits a contiguous PAIR/TRIO: `layer_stack.squash_triggered` (with `trigger_reason="post_publish_depth"` from `maintenance.py:65`) then `layer_stack.squash_completed`, or `layer_stack.squash_failed` on race. Rust `dispatcher.rs:3286-3307 emit_layer_stack_maintenance_audit` emits ONLY `layer_stack.squash_completed`, hardcoding `squash_trigger_reason: "auto_squash"`. So Rust (a) drops the `squash_triggered` and `squash_failed` events entirely, and (b) emits a different reason string than Python's `"post_publish_depth"`. The field exists (`audit/schema.py:51 squash_trigger_reason`; rust value at `dispatcher.rs:3299`). Investigator framed it as "reduced to one event + divergent reason" — correct, but the dropped-events half (no triggered, no failed) is the larger gap. Medium severity is appropriate (audit/observability fidelity, not data-plane correctness). |
| D4 | `remove_layers` drops the `evict_layer_index` call (intentional, benign) | **confirmed** (severity low) | py `stack.py:384-388 _remove_layers` calls `self._view.evict_layer_index(layer.layer_id)`; rs `stack.rs:837-848 remove_layers` does `remove_path` + remove digest file only, no evict. Benign because rs `MergedView` is stateless: `struct MergedView { storage_root } stack.rs:70`, every read resolves off disk (`read_bytes stack.rs:90-122`, `layer_dir stack.rs:141-156`) with no per-layer index cache to invalidate. Confirmed benign. |

## New findings (verifier-discovered)

- **NF1 (low):** Duplicate `AUTO_SQUASH_MAX_DEPTH` constants with mismatched integer
  types — `eos-occ/src/service.rs:19 (u32 = 100)` vs `eos-layerstack/src/lib.rs:66
  (usize = 100)`. Same value; the u32 copy is only consumed by the dead
  `AutoSquashMaintenancePolicy<S>` (D2). Cosmetic redundancy, recommend collapsing to a
  single source once the dead policy is removed. Not a behavioral disparity.
- **NF2 (none/low, corroborating):** Rust's live auto-squash gate replicates Python's
  TWO-STEP check faithfully: `dispatcher.rs:1633 depth() <= MAX || !can_squash(...)` ⇒
  skip, where `can_squash` itself re-plans with `min_reduction=2` (`stack.rs:401`). This
  matches Python `maintenance.py:50,58,60` (depth gate, then `can_squash(max_depth)`),
  EXCEPT the under-lock manifest re-read inside `_squash_lock` (`maintenance.py:56-57`)
  has no analog — the Rust live path relies on the storage-writer exclusive guard inside
  `LayerStack::squash` (`stack.rs:415`) plus the CAS recheck `manifest_prefix_before_plan`
  (`stack.rs:441`) for race safety instead of a separate squash lock. Behaviorally
  equivalent (a lost CAS returns `None` → audited as `raced` in Python / `raced` timing
  in Rust `dispatcher.rs:1676`), so not a disparity, but worth noting the locking shape
  differs from the doc comment's claim (overlaps D2).

## Overall verdict

The CORE squash algorithm is a faithful port: all five load-bearing invariants
(trigger at hardcoded depth **100**; segment-around-lease-heads with identical 0/1/>1
run bucketing; lease-head layers kept and deferred-GC'd only on release; dual `<=`
max_depth + `min_reduction` guards with matching operators and the 1-vs-2 reduction
split between `squash`/`can_squash`; non-destructive manifest pointer-swap with
layers retained on disk until GC) are confirmed_match with bilateral file:line
evidence, and the Rust unit tests at `stack.rs:1282-1362` exercise the deferred-GC
dynamic directly. No false match was found.

The four reported disparities all hold up: D1 (absent shell pre-mount/64-depth
trigger) and D4 (no evict_layer_index, benign) and D2 (dead over-documented policy)
are confirmed; D3 (audit emission reduced to a single `squash_completed` with a
hardcoded `auto_squash` reason, dropping `squash_triggered`/`squash_failed` and
diverging from Python's `post_publish_depth`) is confirmed and slightly adjusted —
the missing-events aspect is broader than "one event with a divergent reason," so it
stays at medium. None of the disparities touch the data-plane squash correctness;
they are a secondary perf trigger (D1), dead code (D2), audit/observability fidelity
(D3), and a benign no-op cache eviction (D4).
