# Squash remount-sweep parallelization — DESIGN

Backed by `RESULTS.md` (run `perf-20260703-052525`). Selected optimization:
**bounded-parallel remount sweep with a phase-split per-session transaction and a
single batched handle persist.** No production implementation is asserted here;
this is the file-level design + safety contract. (Prototype + before/after live
in the same run dir.)

## 1. Objective & safety contract

Cut `T_squash` on hundreds of active sessions without weakening any LayerStack
squash / live-remount guarantee. The sweep is 90–94 % of the op and is serial;
make the independent per-session work overlap. Preserve, unchanged:

- no concurrent top-level `checkpoint_squash` per root (singleflight);
- commit stays serial + transactional; `syncfs`/manifest durability; deterministic
  substitution recording; plan-lease semantics; pin-overlap — **all of these run
  in `stack.squash()` before the sweep and are untouched**;
- per-session admission gates; C1/C5 classification; strict teardown;
- lease release/GC correctness; rollback/fault handling; report classification.

Hard constraint (requested): **do not trade RAM for speed** — see §6.

## 2. Existing algorithm (what we're replacing)

`operation/.../layerstack/service/impls/squash.rs`:

```
outcome = stack.squash();                       // plan+build+commit (6–10% of op)
swept   = session_ids().iter()
              .map(|id| remount_session(id))     // SERIAL sweep (90–94% of op)
              .collect();
```

Each `remount_session` → `remount_workspace` takes the **process-wide**
`Mutex<WorkspaceRuntimeState>` and holds it across the *entire* per-session
transaction: `snapshot → acquire_rewritten_lease → quiesce → subprocess runner →
apply_switch → persist_handles → release_old_lease`. So the sweep is serialized
twice (the loop and the global mutex). Inside a migration (~7.3 ms measured):

- subprocess runner (`namespace.exec.remount_overlay`, `&self`): 32 %
- residual (quiesce + `apply_switch` + `persist_handles` + `release_lease`): 68 %
- `persist_handles` rewrites **all** N handles + 2 fsyncs, **per migrated session**.

### Complexity (current)

`N` live sessions, `M` migrated (`M ≤ N`), `t̄ ≈ 7.3 ms`, `t_id ≈ 0.03 ms`.

| metric | current |
|---|---|
| sweep wall time | `M·t̄ + (N−M)·t_id`, fully serial |
| persist work | `Θ(M·N)` serialized bytes + `2M` fsyncs |
| straggler tail | one 500 ms freeze straggler adds ≤500 ms to the **whole** sweep |
| working memory | `O(1)` (one handle clone + one frozen set) |
| global-lock hold | `Θ(sweep wall time)` — blocks all workspace-runtime ops |

## 3. Proposed algorithm

Two independent changes; (A) is the lever, (B) removes the residual it exposes.

### 3.1 (A) Phase-split the per-session transaction; overlap the middle

`WorkspaceManager::remount_session(&mut self,…)` becomes three parts so the global
lock is held **only** for O(1) in-memory work, and the expensive middle runs
lock-free on a *cloned* handle:

```
snapshot(&self, id)  -> RemountInputs { handle: MountedWorkspace (clone),
                                        runtime: Arc<NamespaceRuntime> }   // under state lock (µs)

execute(inputs, root, cgroup) -> RemountEffect                            // NO state lock
    stack = LayerStack::open(root)                                        //   (per-thread)
    match stack.acquire_rewritten_lease(current, id) {                    //   shared lock → concurrent
        Identity            => return Effect::Identity,
        Err(e)              => return Effect::Leased(lease_rewrite:e),
        Replaced(repl)      => repl,
    }
    if !gate_proven { release repl; return Effect::Leased(kernel_gate) }
    match quiesce(spec_from_handle) {                                     //   only this session's tasks
        Blocked(r)          => release repl; return Effect::Leased(r),
        frozen, pre_id      => …
    }
    report = inputs.runtime.remount_overlay(handle, repl.layer_paths, fresh_workdir) // &self subprocess
    match classify_remount_report(report, …) {                           //   pure fn (C5)
        CleanSkip(r)        => release repl; drop(frozen); Effect::Leased(r),
        Verified{parked}    => drop(frozen);                             //   RESUME immediately (see note)
                               let released = if !parked { stack.release_lease(old) } // exclusive lock
                               Effect::Switch { repl, fresh_workdir, parked, old, released },
        Faulty(d)           => forget(frozen); Effect::Faulty { d, parked_lease: repl.id },
    }

apply(&mut self, id, effect) -> RemountOutcome                            // under state lock (µs, in-memory only)
    Switch => self.apply_switch(id, …); Migrated/Leased(parked)
    Faulty => self.handles[id].parked_lease_id = …; Faulty
    Identity/Leased => passthrough
```

- `execute` touches **no** `&mut self` — only the cloned handle,
  `Arc<NamespaceRuntime>` (`remount_overlay(&self)`), and a per-thread `LayerStack`
  (registry is process-shared, so a lease acquired here is visible to `apply`).
  This is why it can leave the global lock.
- **Resume-in-execute (note):** the MS_MOVE switch is already committed + verified
  in the subprocess, so frozen tasks are resumed the instant the switch verifies —
  *before* the serialized `apply`. This shortens the freeze window (protects the
  HTTP-disconnect budget under fan-out) and avoids sending `FrozenTasks` across
  threads. Safe because the per-session gate is held throughout and running tasks
  never read the daemon handle. Fallback: resume in `apply` (§8).
- **`release_lease` moves into `execute`**, off the global lock. Releases are still
  serialized by the LayerStack per-root exclusive lock ⇒ refcount GC identical to
  serial order; only the *wait* overlaps other sessions' quiesce/runner.

`remount_workspace` (service) orchestrates: `{lock: snapshot} → {no lock: execute}
→ {lock: apply}`. It no longer persists (see B).

### 3.2 (A) Bounded-parallel sweep at the operation layer (gates live here)

```
ctx = obs.context();                             // capture squash trace
swept = parallel_map(ids, W, |id|                // W workers, work-stealing by atomic index
            obs.with_context(ctx.clone(),        // nest child spans under the squash trace
                || remount_session(id)));         // takes THIS session's gate (disjoint) → remount_workspace
```

- `remount_session` is unchanged in behavior: `with_gated_session` (per-session
  gate, disjoint across workers) → `remount_workspace` (now lock-split). §2.3 holds.
- `parallel_map` = `std::thread::scope` + `AtomicUsize` cursor + `W` workers,
  results written into a preallocated `Vec<slot>` by index (order preserved).
  Dependency-free; `W = min(available_parallelism, CAP)` (container = 4 cores;
  work is wait-bound so `W>cores` is legitimate — tune `W ∈ {4,8}`).
- Trace propagation: `TraceContext` is `Clone` + `Send` (`Arc<str>`);
  `obs.with_context` re-enters it per worker so `workspace_session.remount` and
  `namespace.exec.remount_overlay` spans still record (the "after" stays
  measurable).

### 3.3 (B) Batch `persist_handles` to one write per sweep

`apply` no longer persists. After the parallel map, if any session migrated:

```
workspace().persist_handles();                   // ONE write: Θ(N) serialize + 2 fsyncs
```

Safe because `manager.json` is consumed **only** by boot-reap, which reads
`scratch_dir` (= `run_dir`) — and `run_dir` is **invariant** across a remount
(only `dirs.workdir`, a child of it, changes). So per-session persist writes were
redundant for the one field boot cares about. This is an **I/O reduction**, not a
memory trade.

## 4. Concurrency-safety argument (disjointness)

- **Sessions are disjoint:** own holder pid, task set, mount namespace, run_dir,
  lease. `quiesce` SIGSTOP/SIGCONTs only its own tasks; the runner `setns` into its
  own namespaces.
- **Shared state:**
  - `self.handles`: read under state lock (`snapshot`), mutated under state lock
    (`apply`), never touched in `execute`. ✓
  - `Arc<NamespaceRuntime>`: `remount_overlay(&self)`; spawn briefly serializes on
    the existing `SPAWN_CRITICAL_SECTION`, subprocess runs overlap. ✓
  - LayerStack registry: `acquire_rewritten_lease` = shared lock (concurrent);
    `release_lease` = exclusive lock (serialized ⇒ GC identical to serial). ✓
  - Observer sink: thread-safe append, atomic span-id seq; `with_context` per
    worker. `next_handle_id()` atomic. ✓
- **Lock ordering (deadlock-free, strictly better than today):** a worker holds its
  session gate, then takes the state lock *only* in snapshot and apply (released
  between); `execute` takes LayerStack locks with **no state lock held**. The
  current code holds the state lock *across* the LayerStack locks — the new path
  never nests them, so it removes an ordering, it adds none.

## 5. Complexity comparison

| metric | current | proposed |
|---|---|---|
| sweep wall time | `M·t̄ + (N−M)·t_id` (serial) | `⌈M/W⌉·t̄_exec + M·t_apply + N·t_snap + t_persist` |
| — dominant term | `M·t̄` | `⌈M/W⌉·t̄_exec` → ≈ **W× fewer serial units** (Amdahl residual = tiny apply/snapshot/persist) |
| persist work | `Θ(M·N)` bytes + `2M` fsyncs | `Θ(N)` bytes + `2` fsyncs (once) |
| straggler tail | +≤500 ms to whole sweep | +≤500 ms to **one worker**; others proceed |
| global-lock hold | `Θ(sweep)` | `Θ(N·t_snap + M·t_apply + t_persist)` (µs-scale, off the expensive path) |
| working memory | `O(1)` | `O(W)` (W handle clones + W frozen sets in flight); W = fixed constant ≪ N |
| result memory | `O(N)` (API-inherent) | `O(N)` (**unchanged** — same result vector) |

**Measured** (4-core container, `W=4` = `available_parallelism`; see `RESULTS.md`
"Before/after"): 50-session `T_squash` 202.96 → 106.83 ms (1.90×), sweep wall
146–163 → 49–54 ms at 3.6× overlap; 200-session `T_squash` 2141.28 → 311.39 ms
(6.88×), the 70-migration sweep serial-sum 1043 ms → 267 ms wall at **3.91×
overlap** (near-linear on 4 cores), and the 2141 ms freeze-straggler invocation
collapsed to 311 ms (tail isolated). `T_http_disconnect` stayed far under the
1500 ms budget throughout (12–31 ms). Squash smoke 10/10 + medium remount-critical
8/8 PASS.

## 6. Memory guarantee (no RAM-for-speed)

- No caching, memoization, precomputed indexes, or read-ahead buffers.
- No new `O(N)` allocation: the only `O(N)` structure (the swept-results vector)
  already exists in the serial baseline and is required by the result contract
  (blocked-reason attribution needs every disposition).
- Added memory is exactly the **bounded fan-out working set `O(W)`**: at most `W`
  cloned handles + `W` frozen-task sets alive at once, `W` a fixed small constant
  independent of session count. Serial peak is `O(1)`; parallel peak is `O(W)` —
  a constant factor, not a function of load.
- (B) *reduces* transient allocation (one serialization vs `M`).
- The speedup source is **overlapping blocking waits** (subprocess `wait()`,
  freeze poll ≤500 ms, fsync) across cores + **fewer fsyncs** — both orthogonal to
  RAM.

## 7. File-level changes

1. `workspace/src/session/manager.rs` — `runtime: NamespaceRuntime` →
   `Arc<NamespaceRuntime>` (field + constructors). Lets `execute` use it post-unlock.
2. `workspace/src/lifecycle/remount.rs` — split into `remount_snapshot(&self)`,
   free `execute_remount(...)`, `remount_apply(&mut self)`; add `RemountInputs` +
   `RemountEffect`; keep a thin `remount_session(&mut self)` wrapper (snapshot →
   execute → apply → persist) for existing single-call/unit-test callers. Resume
   frozen + release old lease inside `execute`.
3. `workspace/src/service/impls/remount_workspace.rs` — `remount_workspace` uses
   the split (lock only around snapshot & apply; **no persist**); add
   `WorkspaceRuntimeService::persist_handles(&self)` for the batched call.
4. `operation/.../workspace_session/service/impls/remount_session.rs` — unchanged
   behavior (gate + remount_workspace + `refresh_session_handle`); benefits from the
   lock split.
5. `operation/.../layerstack/service/impls/squash.rs` — serial `.map` →
   `parallel_map(ids, W, …)` with `obs.with_context`; after the sweep, one
   `workspace().persist_handles()` if any migrated. Module-local `parallel_map`
   (std threads, atomic cursor, indexed slots).

## 8. Validation & fallback

Unit (crate `tests/`):
- snapshot∘execute∘apply == old `remount_session` for Identity/Migrated/Leased/
  Faulty (table test via `WorkspaceRuntimeHooks` fakes);
- `execute_remount` provably ignores `self.handles`;
- `parallel_map`: every item processed once, order preserved, ≤ W in flight.

Live E2E:
- LOAD-COMBO-HTTP (50 + 200, `max_file_bytes` raised for full span capture):
  `T_squash` down, `T_http_disconnect` < 1500 ms, correctness/space/teardown PASS;
- LOAD-499-HTTP, LOAD-LARGE-HTTP regression;
- SMK/MED remount cases: migrate, pin/leased, faulty, MED-20 quiesce-at-100-tasks,
  MED-09 escaped-pgid, MED-17 persist-failure-still-migrates.

Risks / fallback:
- Resume-in-execute change → fallback resume-in-apply (needs `FrozenTasks: Send`).
- Batched persist crash-window → analysis shows no correctness impact (reap reads
  only remount-invariant `scratch_dir`); fallback per-session persist (keep
  parallelism, lose fsync-batching).
- 4-core cap on CPU-bound share → mitigated by wait-bound `W>cores`.
- If parallel remount proves too invasive → ship (B) + release-lease-off-lock
  alone for a partial serial-cost reduction; parallelism remains the main lever.
