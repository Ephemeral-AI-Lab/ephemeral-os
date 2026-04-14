# Plan: REPLANNING Status + Dependent Rewiring

## Summary

When a developer/validator fails and requests a replan, its dependents should NOT be launched or cascade-cancelled. They should wait until the replan outcome is settled.

**Changes:** 1 new status (`REPLANNING`), 1 new field (`fired_by_task_id`), dep rewiring in `apply_replan`.

## Lifecycle

```
Task X fails, requests replan
  -> X.status = REPLANNING (not FAILED, not terminal)
  -> X's dependents stay PENDING (pending_dep_count unchanged, no cascade)
  -> Replanner R created (R.fired_by_task_id = X.id)

R completes:
  add_tasks [T1, T2]       -> rewire: X's dependents now depend on [T1, T2] instead of X -> mark X FAILED
  cancel_and_redraft [R1]   -> rewire: X's dependents now depend on [R1] instead of X -> mark X FAILED
  declare_blocker           -> X stays REPLANNING, blocker system runs, post-fix replanner eventually does add_tasks/cancel_and_redraft
  R fails (no action)       -> mark X FAILED + cascade (give up)
```

---

## Step 1 — Add REPLANNING status to TaskStatus enum

**File:** `backend/src/team/models.py:35`

Add `REPLANNING = "replanning"` after `PAUSED`.

Not in `TERMINAL_STATUSES` (L55), so:
- `_cascade_recursive_sql` won't cascade through it (only cascades from terminal)
- `mark_done` promotion won't fire (dependents keep X in their pending count)
- `pop_ready` in dispatch_queue won't pick it up (it's not "ready")

---

## Step 2 — Add fired_by_task_id column to TaskRecord

**File:** `backend/src/team/persistence/task_record.py:64`

Add after `blocker_id`:
```python
fired_by_task_id: Mapped[str | None] = mapped_column(Text, nullable=True)
```

**File:** `backend/src/team/models.py:144`

Add to `Task` dataclass:
```python
fired_by_task_id: str | None = None
```

**File:** `backend/src/team/persistence/task_store.py:22`

Add to `record_to_task`:
```python
fired_by_task_id=getattr(rec, "fired_by_task_id", None),
```

---

## Step 3 — request_replan: mark REPLANNING instead of FAILED

**File:** `backend/src/team/persistence/task_store.py:897`

Change:
```python
# Before:
.values(status="failed", finished_at=func.now(), failure_reason=f"replan_requested: {reason}")

# After:
.values(status="replanning", failure_reason=f"replan_requested: {reason}")
# No finished_at -- task isn't finished yet
```

Also handle the case where the task is already REPLANNING (for the post-fix replanner path). If `current_status == "replanning"`, skip the status update and just create the new replanner.

**File:** `backend/src/team/persistence/task_store.py:915`

Set `fired_by_task_id` on replanner TaskRecord:
```python
replanner = TaskRecord(
    ...
    fired_by_task_id=task_id,   # NEW
)
```

**File:** `backend/src/team/persistence/task_graph.py:161`

Change in-memory graph:
```python
# Before:
original.status = TaskStatus.FAILED

# After:
original.status = TaskStatus.REPLANNING
```

---

## Step 4 — Add rewire_dependents to TaskStore

New method on `TaskStore`:

```python
async def rewire_dependents(
    self, original_task_id: str, new_dep_ids: list[str]
) -> list[str]:
```

Logic (single transaction):
1. SELECT all tasks WHERE `deps @> ARRAY[original_task_id]` AND status NOT IN terminal statuses
2. For each dependent D:
   - Remove `original_task_id` from `D.deps`
   - Append `new_dep_ids` to `D.deps`
   - Recalculate `pending_dep_count` = count of deps NOT in DONE status (from scratch, not arithmetic)
   - If `pending_dep_count` drops to 0 and status is "pending" -> promote to "ready"
3. Mark `original_task_id` as FAILED + `finished_at=now()` (terminal, but nothing depends on it anymore)
4. Refresh in-memory graph
5. Return list of rewired dependent IDs (and any promoted IDs)

---

## Step 5 — Wire rewiring into apply_replan flow

**File:** `backend/src/team/task_center.py:329`

Modify `apply_replan`. After `self._expander.apply_replan(...)` returns:

```python
async def apply_replan(self, replan_task_id, add_tasks, cancel_ids, ...):
    # existing: call expander
    outcome = await self._expander.apply_replan(...)

    # NEW: if this replanner was fired by a REPLANNING task, rewire deps
    replanner_rec = await self._store.get_record(replan_task_id)
    if replanner_rec and replanner_rec.fired_by_task_id:
        original_rec = await self._store.get_record(replanner_rec.fired_by_task_id)
        if original_rec and original_rec.status == "replanning":
            new_task_ids = outcome.get("inserted_ids", [])
            if new_task_ids:
                rewired = await self._store.rewire_dependents(
                    replanner_rec.fired_by_task_id, new_task_ids
                )
            else:
                # empty replan = give up
                await self._store.fail_with_cascade(
                    replanner_rec.fired_by_task_id, "replan_produced_no_tasks"
                )

    await self._transitions.refresh_and_emit(before)
    return outcome
```

**File:** `backend/src/team/planning/expander.py:237`

Modify return to include inserted IDs:

```python
# Before:
return {"added": len(specs), "cancelled": len(cancel_ids)}

# After:
return {"added": len(specs), "cancelled": len(cancel_ids), "inserted_ids": [r.id for r in inserted]}
```

---

## Step 6 — Replanner failure fallback

**File:** `backend/src/team/task_center.py:296`

Modify `fail`. When a replanner fails, also fail its origin:

```python
async def fail(self, task_id: str, reason: str) -> None:
    # NEW: if this is a replanner failing, also fail its origin
    rec = await self._store.get_record(task_id)
    if rec and rec.fired_by_task_id:
        origin = await self._store.get_record(rec.fired_by_task_id)
        if origin and origin.status == "replanning":
            await self._store.fail_with_cascade(
                rec.fired_by_task_id, f"replanner_failed: {reason}"
            )

    # existing fail logic
    before = self._transitions.snapshot()
    warnings = await self._store.fail_task(task_id, reason)
    ...
```

---

## Step 7 — declare_blocker path

When the replanner declares a blocker (via executor `_dispatch` -> `BlockerDeclaration`), the replanner itself completes (marked DONE via `tc.complete_task`). The original stays REPLANNING.

The conductor eventually resolves the blocker -> calls `_spawn_post_fix_replanner` -> calls `tc.request_replan(blocker.initiating_task_id, ...)`.

### Problem

`blocker.initiating_task_id` is the replanner task, not the original failed task. The post-fix replanner needs to know the original.

### Fix

**File:** `backend/src/team/runtime/conductor.py:278`

In `_spawn_post_fix_replanner`, look up the initiating task's `fired_by_task_id` and use that as the replan target instead:

```python
async def _spawn_post_fix_replanner(self, blocker, fix_summary):
    tc = self._team_run.task_center
    # Find the original task that started this chain
    initiating_rec = await tc.store.get_record(blocker.initiating_task_id)
    replan_target = blocker.initiating_task_id
    if initiating_rec and initiating_rec.fired_by_task_id:
        replan_target = initiating_rec.fired_by_task_id
    await tc.request_replan(replan_target, request)
```

**File:** `backend/src/team/persistence/task_store.py:897`

`request_replan` must handle already-REPLANNING tasks (from step 3). If the task is already REPLANNING, skip the status update and just create the new replanner with `fired_by_task_id` pointing to the original.

---

## Step 8 — Cascade safety (no changes needed)

`_cascade_recursive_sql` seeds from a task marked FAILED and walks through dependents. Since REPLANNING is NOT terminal and we don't mark it FAILED until after rewiring, cascades won't touch dependents of REPLANNING tasks.

The CTE join condition checks `descendant.status.in_(("pending", "ready", "expanded"))` and `descendant.deps.any(dep_chain.c.id)`. A REPLANNING task won't be seeded because it's not FAILED at cascade time.

**Safe. No changes needed.**

---

## Validation rules per action

| Action | Validation |
|--------|-----------|
| `add_tasks` with tasks | Must produce >= 1 task. New tasks get wired as replacement deps. Cycle check (existing). |
| `add_tasks` empty | Treat as replanner failure -> fall back to FAILED + cascade on original |
| `cancel_and_redraft` | Must have >= 1 replacement task (same empty check). Cancelled tasks validated as today (must be siblings, must be pending/ready/expanded). |
| `declare_blocker` | Original stays REPLANNING. No rewiring yet. Post-fix replanner inherits `fired_by_task_id`. Eventually resolves via add_tasks/cancel_and_redraft. |
| Replanner crashes | `fail()` detects `fired_by_task_id` -> fails original with cascade |

---

## Files changed (8 files)

| File | Change |
|------|--------|
| `backend/src/team/models.py` | Add `REPLANNING` to TaskStatus, add `fired_by_task_id` to Task |
| `backend/src/team/persistence/task_record.py` | Add `fired_by_task_id` column |
| `backend/src/team/persistence/task_store.py` | Modify `request_replan` (REPLANNING not FAILED), add `rewire_dependents`, update `record_to_task` |
| `backend/src/team/persistence/task_graph.py` | Change `apply_replan` to set REPLANNING |
| `backend/src/team/task_center.py` | Modify `apply_replan` (call rewire after expander), modify `fail` (detect replanner failure) |
| `backend/src/team/planning/expander.py` | Return `inserted_ids` from `apply_replan` |
| `backend/src/team/runtime/executor.py` | No changes needed (existing flow handles it) |
| `backend/src/team/runtime/conductor.py` | Thread `fired_by_task_id` through blocker -> post-fix replanner |

---

## Edge cases

| Case | Handling |
|------|----------|
| Replanner fails | `fail()` detects `fired_by_task_id` -> marks original FAILED + cascade |
| Empty add_tasks | Treat as failure, cascade |
| New replacement task also fails + replans | Works naturally -- its own REPLANNING cycle, independent of original |
| Multiple deps where one is REPLANNING | Dependent stays PENDING, other deps completing doesn't unblock it |
| Original has no dependents | Rewiring is a no-op, just mark FAILED |
| Blocker path: second replanner | Must carry original's `fired_by_task_id`, not first replanner's |
| request_replan on already-REPLANNING task | Skip status update, just create new replanner |
