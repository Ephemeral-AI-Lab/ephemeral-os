# Task Center Redesign — Key Changes Summary

**Date:** 2026-04-14  
**Source documents:**
- [TaskCenter + DAG Unification](task-center-dag-unification.md)
- [Dynamic Replanning Blocker Protocol](dynamic-replanning-blocker-protocol.md)
- [TaskCenter Active Mode](task-center-active-mode.md)

---

## What changes

### 1. TaskCenter becomes the single owner of task lifecycle

**Before:** Three components split task management — TaskCenter (notes), Dispatcher (orchestration), DispatcherStore (SQL persistence). Every consumer bridges the gap. context_for needs a task_lookup callback to reach the DAG. read_sibling_notes needs a dispatcher_store to resolve subtrees. The executor mediates between all three on every event.

**After:** TaskCenter absorbs both Dispatcher and DispatcherStore. It owns task records, dependencies, status transitions, plan insertion, cascade operations, notes, and context building. Consumers call one API.

**Key signature changes:**
- `context_for(task, max_context_bytes)` — no more task_lookup or file_change_store callbacks
- `read_sibling_notes(parent_id, keyword, scope_paths)` — no more dispatcher_store parameter

**Deleted:** `dispatcher.py`, `dispatcher_store.py`

**New:** `dispatch_queue.py` — thin component (~50 lines) with only `pop_ready` + `claim`, extracted for SQL atomicity (FOR UPDATE SKIP LOCKED)

### 2. Blocker-aware pause/fix/resume replaces independent retries

**Before:** When a completed task breaks shared code, every sibling fails independently. Each retries, each fails again, each triggers a separate replan. No mechanism detects "these failures share a root cause."

**After:** Three roles with strict separation:
- **Developer** — reports failure via `request_replan`. No blocker awareness.
- **Replanner** — assesses and decides. Three actions only: `add_tasks`, `declare_blocker`, `cancel_and_redraft`.
- **Conductor** — executes blocker mechanics. Zero LLM calls. Deterministic and fully testable.

**Blocker lifecycle:** declare -> pause siblings (scope-based) -> assess running agents via EphemeralTask -> spawn fix task -> fix completes -> resume paused agents with checkpoint rehydration.

**DispatchQueue integration:** `pop_ready` accepts a `blocker_guard` callable from the Conductor. During an active blocker, new tasks are prevented from dispatching without mutating their state.

### 3. TaskCenter active mode replaces passive nudges

**Before:** An edit-based nudge in query.py injects a SystemReminderBlock hoping the agent calls post_note. The agent may ignore it. 24 lines of inline counter logic in query.py + 16 lines in daytona tools.

**After:** TaskCenter owns content quality. It tracks agent activity and spawns EphemeralTasks to produce notes on agents' behalf. The agent is never interrupted. The note is guaranteed.

**Two triggers:**
- **Edit counter** — threshold 5 edits since last note. Resets on post_note only. EphemeralTask prompt: "what files were edited and why."
- **Turn counter** — threshold 10 turns since last posthook. Resets on any posthook call. EphemeralTask prompt: "status, findings, and blockers."

**Critical design point:** The turn prompt explicitly asks about blockers. This ensures that even silent agents surface systemic failures. The replanner sees these via read_sibling_notes — enabling early blocker detection across the full sibling set.

**Note attribution:** auto-generated notes use `agent_name + " (auto)"` suffix, posted under the original task's ID and scope_paths.

**query.py impact:** One optional `on_turn` callback added to `run_query_loop`. No message injection, no display_messages mutation.

---

## How the three designs connect

```
TaskCenter (unified)
    owns task lifecycle, notes, context, planning
        |
        +-- Active Mode (auto-notes)
        |       monitors edit/turn activity
        |       spawns EphemeralTask for silent agents
        |       surfaces blockers early
        |
        +-- Blocker Protocol
                replanner reads sibling notes (including auto-notes)
                declares blocker when systemic pattern detected
                Conductor pauses/resumes via TaskCenter
                fix task resolves root cause once

DispatchQueue (thin)
    pop_ready + claim only
    accepts blocker_guard from Conductor
```

Active mode feeds the blocker protocol: auto-generated notes surface shared failures that agents failed to report. The replanner sees the pattern via read_sibling_notes and declares a blocker. The Conductor executes mechanically through TaskCenter. One fix resolves the root cause for all paused siblings.

---

## Executor before/after

**Before (5 references, 6 calls per task):**
```
task = dispatcher.store.pop_ready(run_id)
dispatcher.mark_running(task.id, agent_run_id)
ctx = task_center.context_for(task, file_change_store, task_lookup, max_bytes)
result = run_agent(ctx)
dispatcher.complete(task.id, result)
task_center.post(completion_note)
```

**After (3 references, 3 calls per task):**
```
task = dispatch_queue.pop_ready(run_id, conductor.guard_pop_ready)
ctx = task_center.context_for(task, max_bytes)
result = run_agent(ctx)
task_center.complete_task(run_id, task.id, result)
```

---

## Files impact

| Action | Files |
|--------|-------|
| Modified | task_center.py, executor.py, conductor.py, team_run.py, context_builder.py |
| New | dispatch_queue.py (~50 lines) |
| Deleted | dispatcher.py (~640 lines), dispatcher_store.py (~840 lines) |
| Untouched | models.py, task_record.py, schema.sql, query.py, posthook tools, ephemeral_task module |
