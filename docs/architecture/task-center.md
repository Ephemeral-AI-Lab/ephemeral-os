# Task Center

> **Note:** This document consolidates the TaskCenter redesign. For detailed architecture rationale, see:
> - [Task Center + DAG Unification](../design/task-center-dag-unification.md)
> - [Dynamic Replanning Blocker Protocol](../design/dynamic-replanning-blocker-protocol.md)
> - [Task Center Active Mode](../design/task-center-active-mode.md)
> - [Task Center Redesign Summary](../design/task-center-redesign-summary.md)

---

## Role & Ownership

TaskCenter is the single owner of task lifecycle: task records, dependencies, status transitions, plan insertion, cascade operations, notes (in-memory), context building, blocker coordination, and auto-note generation. It absorbs the former Dispatcher and DispatcherStore. Consumers call one unified API instead of bridging three components.

DispatchQueue is a thin extraction (~60 lines): atomic task claiming via `pop_ready()` with SQL `FOR UPDATE SKIP LOCKED`. TaskCenter calls `mark_running()` after the queue returns a task.

---

## Class Relationship Diagram

```
┌──────────────────────────────────────────────────────────────┐
│                         TaskCenter                           │
│  - _store: TaskStore                                         │
│  - _notes: list[Note]                                        │
│  - _activity_counters: dict                                  │
│  - _team_run_id: str                                         │
│  + post(note: Note)                                          │
│  + read(authors, scope_paths, keyword)                       │
│  + context_for(task) → str                                   │
│  + read_sibling_notes(parent_id) → str                       │
│  + mark_running(task_id, agent_run_id) → Task                │
│  + complete_task(task_id, result) → list[Task]               │
│  + fail(task_id, reason)                                     │
│  + pause_running_task(task_id, blocker_id, verdict)          │
│  + resume_paused_tasks(blocker_id) → int                     │
│  + cancel_paused_tasks(blocker_id) → int                     │
│  + request_replan(task_id, request) → Task                   │
│  + apply_replan(replan_id, add_tasks, cancel_ids)            │
│  + on_edit(task_id, file_path)                               │
│  + on_posthook(task_id)                                      │
│  + tick(task_id)                                             │
│  + should_checkpoint(task_id) → str|None                     │
│  + check(task_id, snapshot, api_client, model) → bool        │
└──────────────┬───────────────────────────┬───────────────────┘
               │ delegates SQL             │ owns notes
               ▼                           ▼
┌──────────────────────────┐   ┌───────────────────────────┐
│        TaskStore         │   │           Note            │
│  - session_factory       │   │  task_id: str             │
│  - team_run_id           │   │  agent_name: str          │
│  + insert_plan(...)      │   │  content: str             │
│  + get_task(task_id)     │   │  timestamp: float         │
│  + get_all_tasks()       │   │  scope_paths: list[str]   │
│  + get_adjacency()       │   └───────────────────────────┘
│  + _mark_done(...)       │
│  + _fail_task_sql(...)   │
│  + _pause_running_task.. │
│  + _resume_paused_tasks. │
│  + _cancel_by_ids_sql(.) │
└──────────────────────────┘

┌──────────────────────────┐         ┌───────────────────────────────┐
│      DispatchQueue       │         │           Conductor           │
│  - session_factory       │         │  - _team_run                  │
│  + pop_ready(run_id)     │         │  - _executor_snapshots        │
│    → TaskRecord|None     │         │  + create_blocker(            │
└──────────┬───────────────┘         │      initiating_task_id,      │
           │ reads task state        │      reason,                  │
           │ (via DispatchQueue)     │      root_cause_paths)        │
           ▼                         └──────────────┬────────────────┘
┌──────────────────────────────────────────┐        │ pause/resume tasks,
│                 Executor                 │        │ post notes
│  - task_center                           │        │
│  - dispatch_queue                        │        ▼
│  - conductor                             │   TaskCenter
│  + _run_one(task)                        │
│  + _dispatch(task, result)               │
└──────────────────────────────────────────┘
  │ mark_running, context_for, complete_task
  ▼
TaskCenter
  │ pop_ready
  ▼
DispatchQueue
```

---

## Task Lifecycle States

```
                    ┌─────────┐
                    │  [*]    │
                    └────┬────┘
                         │ inserted in plan
                         ▼
                    ┌─────────┐
                    │ PENDING │◄──────────────────────────────────────┐
                    └────┬────┘                                       │
                         │ dependencies satisfied                     │
                         ▼                                            │
                    ┌─────────┐                                       │
              ┌────►│  READY  │◄──────────────────────────┐          │
              │     └────┬────┘                           │          │
              │          │ pop_ready claimed by executor  │          │
              │          ▼                                │          │
              │     ┌─────────┐                           │          │
              │     │ RUNNING │                           │          │
              │     └────┬────┘                           │          │
              │          │                                │          │
        ┌─────┴──┬───────┴──────┬────────────┐           │          │
        │        │              │            │           │          │
        ▼        ▼              ▼            ▼           │          │
  ┌──────────┐ ┌────────┐ ┌──────────┐ ┌────────┐       │          │
  │   DONE   │ │ FAILED │ │ EXPANDED │ │ PAUSED │       │          │
  └──────┬───┘ └───┬────┘ └────┬─────┘ └───┬────┘       │          │
         │         │           │            │            │          │
         │         │           │ all        │ blocker    │          │
         │         │           │ children   │ resolved   │          │
         │         │           │ completed  └────────────┘          │
         │         │           ▼                                     │
         │         │      ┌──────────┐                               │
         │         │      │   DONE   │                               │
         │         │      └──────┬───┘                               │
         │         │             │                                   │
         ▼         ▼             ▼                                   │
       ┌───┐     ┌───┐         ┌───┐                                 │
       │[*]│     │[*]│         │[*]│                                 │
       └───┘     └───┘         └───┘                                 │
                                                                     │
  PAUSED ──── blocker fix failed ────► CANCELLED ──► [*]            │
  READY  ──── cascade / cancel_by_ids ► CANCELLED ──► [*]           │
  PENDING ─── cascade / cancel_by_ids ► CANCELLED ──► [*]           │

  Notes:
  - Only RUNNING tasks can pause. READY/PENDING tasks are unaffected during a blocker.
  - Parent stays EXPANDED while any child is PAUSED.
  - If result.submitted_plan exists, insert_plan() is called and task becomes EXPANDED.
```

---

## Context Building for Agents

```
  ┌──────────────────────────────────────────────────┐
  │   task_center.context_for(task, max_context_bytes)│
  └──────────────────────┬───────────────────────────┘
                         │ What to include?
           ┌─────────────┼─────────────────────────────────┐
           │             │             │                   │
           ▼             ▼             ▼                   ▼
  ┌────────────────┐ ┌──────────┐ ┌──────────────┐ ┌──────────────────┐
  │ Retry context  │ │  Task    │ │  Self notes  │ │ Dependency notes │
  │ If retry_count │ │  desc.   │ │ Read all     │ │ Walk deps, read  │
  │ > 0: previous  │ │ task.    │ │ notes from   │ │ notes from each  │
  │ failure reason │ │ instruct.│ │ this task    │ │ dep              │
  └───────┬────────┘ └────┬─────┘ └──────┬───────┘ └────────┬─────────┘
          │               │              │                   │
          │       ┌───────┘              │          ┌────────┘
          │       │         ┌────────────┘          │
          │       │         │                       │
          ▼       ▼         ▼                       ▼
         ┌─────────────────────────────────────────────┐
         │   ┌──────────────┐   ┌──────────────────┐   │
         │   │ File changes │   │  Parent chain    │   │
         │   │ FileChange   │   │  Walk ancestors, │   │
         │   │ Store: files │   │  read notes from │   │
         │   │ since create │   │  each parent     │   │
         │   └──────┬───────┘   └────────┬─────────┘   │
         └──────────┼────────────────────┼─────────────┘
                    │                    │
                    ▼                    ▼
         ┌──────────────────────────────────────────────┐
         │  Build priority-ordered context string       │
         │  respecting max_context_bytes                │
         └────────────────────┬─────────────────────────┘
                              │
                              ▼
                    ┌──────────────────┐
                    │ Return context:  │
                    │      str         │
                    └──────────────────┘
```

---

## Blocker Protocol Lifecycle

The blocker protocol detects when a systemic failure affects multiple siblings and coordinates a single fix before resuming.

```
  Running Agent      TaskCenter         Replanner        Conductor        Resolver       Resumed Agents
       │                 │                  │                │               │                 │
       │ request_replan( │                  │                │               │                 │
       │   task_id,      │                  │                │               │                 │
       │   reason)       │                  │                │               │                 │
       │────────────────►│                  │                │               │                 │
       │                 │ Mark task FAILED │                │               │                 │
       │                 │ spawn replanner  │                │               │                 │
       │                 │─────────────────►│                │               │                 │
       │                 │                  │ read_sibling_  │               │                 │
       │                 │                  │ notes(parent_id│               │                 │
       │                 │◄─────────────────│                │               │                 │
       │                 │                  │ Assess: shared │               │                 │
       │                 │                  │ root cause?    │               │                 │
       │                 │                  │                │               │                 │
       │                 │ declare_blocker( │                │               │                 │
       │                 │   reason,        │                │               │                 │
       │                 │   root_cause_pth)│                │               │                 │
       │                 │◄─────────────────│                │               │                 │
       │                 │ Record blocker   │                │               │                 │
       │                 │ status=ASSESSING │                │               │                 │
       │                 │                  │                │               │                 │
       │                 │ get_siblings_and │                │               │                 │
       │                 │ _descendants(    │                │               │                 │
       │                 │   task_id)       │                │               │                 │
       │                 │◄─────────────────────────────────│               │                 │
       │                 │                  │                │ Determine     │                 │
       │                 │                  │                │ scope: all    │                 │
       │                 │                  │                │ siblings +    │                 │
       │                 │                  │                │ subtrees      │                 │
       │                 │                  │                │               │                 │
       │                 │        ┌─────────────────────┐   │               │                 │
       │                 │        │ For each RUNNING    │   │               │                 │
       │                 │        │ sibling:            │   │               │                 │
       │                 │        │ assess_pause(snap.) │   │               │                 │
       │                 │        │ (external trigger   │   │               │                 │
       │                 │        │  evaluates pause)   │   │               │                 │
       │                 │        └─────────────────────┘   │               │                 │
       │                 │ pause_running_task(              │               │                 │
       │                 │   task_id, blocker_id, verdict)  │               │                 │
       │                 │◄─────────────────────────────────│               │                 │
       │                 │ Mark task PAUSED │                │               │                 │
       │                 │ store checkpoint │                │               │                 │
       │                 │ + verdict        │                │               │                 │
       │                 │                  │                │               │                 │
       │                 │                  │                │ spawn_resolver(               │
       │                 │                  │                │   root_cause_paths,           │
       │                 │                  │                │   blocker_id)                 │
       │                 │                  │                │──────────────►│               │
       │                 │                  │                │               │ Fix task:     │
       │                 │                  │                │               │ repair files  │
       │                 │                  │                │               │               │
       │                 │ complete_task(   │                │               │               │
       │                 │   fix_task_id,   │                │               │               │
       │                 │   result)        │                │               │               │
       │                 │◄──────────────────────────────────────────────────               │
       │                 │ Mark blocker     │                │               │               │
       │                 │ RESOLVED         │                │               │               │
       │                 │                  │                │               │               │
       │                 │ resume_paused_tasks(blocker_id)   │               │               │
       │                 │◄─────────────────────────────────│               │               │
       │                 │ PAUSED → READY   │                │               │               │
       │                 │                  │                │               │               │
       │                 │ mark_running(task_id, agent_run_id)               │               │
       │                 │◄──────────────────────────────────────────────────────────────────│
       │                 │                  │                │               │ Rehydrate     │
       │                 │                  │                │               │ checkpoint,   │
       │                 │                  │                │               │ continue from │
       │                 │                  │                │               │ where paused  │
```

---

## Active Mode Auto-Note Generation

Active mode spawns external-trigger agents to post notes on behalf of silent agents, ensuring blockers are surfaced early.

```
    Executor           TaskCenter         ExtTrig         AutoAgent      TaskCenter.post()
       │                   │                 │                │                 │
       │ on_edit(task_id,  │                 │                │                 │
       │   file_path)      │                 │                │                 │
       │──────────────────►│                 │                │                 │
       │                   │ Increment       │                │                 │
       │                   │ edits_since_note│                │                 │
       │                   │                 │                │                 │
       │ on_posthook(      │                 │                │                 │
       │   task_id)        │                 │                │                 │
       │──────────────────►│                 │                │                 │
       │                   │ Reset turns_    │                │                 │
       │                   │ since_posthook  │                │                 │
       │                   │ to 0            │                │                 │
       │                   │                 │                │                 │
       │  ┌──────────────────────────────┐  │                │                 │
       │  │ Every turn:                  │  │                │                 │
       │  │ tick(task_id)                │  │                │                 │
       │  │ ──────────────────────────►  │  │                │                 │
       │  │ Increment turns_since_posthk │  │                │                 │
       │  └──────────────────────────────┘  │                │                 │
       │                   │                 │                │                 │
       │ check(task_id,    │                 │                │                 │
       │   snapshot,       │                 │                │                 │
       │   api_client,     │                 │                │                 │
       │   model)          │                 │                │                 │
       │──────────────────►│                 │                │                 │
       │                   │                 │                │                 │
       │        ┌──────────┴───────────┐     │                │                 │
       │        │ edits_since_note >= 5│     │                │                 │
       │        │ EDIT threshold       │     │                │                 │
       │        │ crossed?             │     │                │                 │
       │        │                      │     │                │                 │
       │        │ OR                   │     │                │                 │
       │        │ turns_since_posthook │     │                │                 │
       │        │ >= 10?               │     │                │                 │
       │        │ TURN threshold       │     │                │                 │
       │        │ crossed?             │     │                │                 │
       │        └──────────┬───────────┘     │                │                 │
       │                   │                 │                │                 │
       │     ┌─────────────┴──────────────┐  │                │                 │
       │     │ Neither threshold crossed  │  │                │                 │
       │     │ return False ──────────────┼──┼────────────────┼─────────────────┼──► Executor
       │     └────────────────────────────┘  │                │                 │
       │                   │                 │                │                 │
       │                   │ run_checkpoint_ │                │                 │
       │                   │ note(prompt=    │                │                 │
       │                   │ EDIT/TURN_      │                │                 │
       │                   │ CHECKPOINT_     │                │                 │
       │                   │ PROMPT)         │                │                 │
       │                   │────────────────►│                │                 │
       │                   │                 │ Create         │                 │
       │                   │                 │ ephemeral      │                 │
       │                   │                 │ agent w/       │                 │
       │                   │                 │ snapshot +     │                 │
       │                   │                 │ PostNoteTool   │                 │
       │                   │                 │───────────────►│                 │
       │                   │                 │                │ tool_choice=    │
       │                   │                 │                │ "any", retry    │
       │                   │                 │                │ until success   │
       │                   │                 │                │                 │
       │                   │                 │                │ post_note(      │
       │                   │                 │                │   content)      │
       │                   │                 │                │────────────────►│
       │                   │                 │                │                 │ Post note under
       │                   │                 │                │                 │ original task,
       │                   │                 │                │                 │ agent_name =
       │                   │                 │                │                 │ "name (auto)"
       │                   │                 │                │                 │
       │                   │ on_note_posted(note)             │                 │
       │                   │◄─────────────────────────────────────────────────│
       │                   │ Reset both      │                │                 │
       │                   │ counters to 0   │                │                 │
       │                   │ (ignore system  │                │                 │
       │                   │ notes)          │                │                 │
       │                   │                 │                │                 │
       │◄──────────────────│ return True     │                │                 │
       │                   │                 │                │                 │
       │  Note: Turn prompt explicitly asks: "Is the agent blocked             │
       │  by code another task broke?" — surfaces blockers early.              │
```

---

## Blocker Assessment — Determining Pause Verdicts

```
  ┌────────────────────────────────────────────┐
  │   Conductor._assess_running(blocker_id)    │
  └─────────────────────┬──────────────────────┘
                        │
                        ▼
  ┌────────────────────────────────────────────┐
  │ Get scope: get_siblings_and_descendants()  │
  └─────────────────────┬──────────────────────┘
                        │
                        ▼
  ┌────────────────────────────────────────────┐
  │         Filter RUNNING tasks only          │
  └─────────────────────┬──────────────────────┘
                        │
                        ▼
  ┌────────────────────────────────────────────┐
  │          For each RUNNING task             │
  └─────────────────────┬──────────────────────┘
                        │
                        ▼
  ┌────────────────────────────────────────────┐
  │       Spawn external_trigger agent         │
  └─────────────────────┬──────────────────────┘
                        │
                        ▼
  ┌────────────────────────────────────────────┐
  │  Inputs:                                   │
  │  - task snapshot                           │
  │  - running agent's messages                │
  │  - blocker reason + root_cause_paths       │
  └─────────────────────┬──────────────────────┘
                        │
                        ▼
  ┌────────────────────────────────────────────┐
  │       PauseVerdictTool: Yes / No / Unclear │
  └──────┬───────────────┬────────────────┬────┘
         │ Yes           │ No             │ Unclear
         ▼               ▼                ▼
  ┌─────────────┐ ┌────────────────┐ ┌───────────────────────┐
  │ pause_      │ │ Leave status=  │ │ Leave status=RUNNING  │
  │ running_    │ │ RUNNING        │ │ May fail naturally,   │
  │ task()      │ │ Task can       │ │ trigger request_      │
  │ status=     │ │ continue       │ │ replan, replanner     │
  │ PAUSED      │ └───────┬────────┘ │ sees blocker context  │
  └──────┬──────┘         │          └──────────┬────────────┘
         │                │                     │
         ▼                ▼                     ▼
  ┌─────────────┐ ┌──────────────────────────────────────────┐
  │ Store:      │ │         Task continues normally          │
  │ blocker_id, │ └──────────────────────────────────────────┘
  │ pause_      │
  │ checkpoint, │
  │ verdict     │
  └─────────────┘
```

---

## DispatchQueue Separation

DispatchQueue is extracted for SQL atomicity only. It has one method:

- `pop_ready(run_id)` — atomic claim of the next READY task using `FOR UPDATE SKIP LOCKED`

TaskCenter handles everything else: `mark_running()`, status transitions, plan insertion, cascade operations, notes, context, blocker coordination.

**No dispatch guard:** Tasks pop freely during active blockers. Tasks that hit the broken dependency fail naturally and trigger `request_replan()`. The replanner reads sibling notes (including auto-notes) and sees the existing blocker context, enabling informed recovery decisions.

---

## Summary of Key Methods

**Task Lifecycle:**
- `mark_running(task_id, agent_run_id)` — Transition RUNNING, charge budget
- `complete_task(task_id, result)` — Mark DONE, decrement pending_dep_count, promote parent, handle plan expansion
- `fail(task_id, reason)` — Mark FAILED, cascade cancel dependents
- `retry_task(task_id, request)` — Reset to READY if retries remaining, else FAILED
- `request_replan(task_id, request)` — Mark FAILED, spawn replanner task
- `apply_replan(replan_id, add_tasks, cancel_ids)` — Validate, cancel, and insert new tasks

**Blocker Protocol:**
- `pause_running_task(task_id, blocker_id, checkpoint, verdict)` — Transition PAUSED
- `resume_paused_tasks(blocker_id)` — Bulk transition PAUSED → READY
- `cancel_paused_tasks(blocker_id)` — Cancel all PAUSED tasks (on fix failure)

**Context & Notes:**
- `context_for(task, max_context_bytes)` — Build context string from deps, notes, file changes, parent chain
- `read_sibling_notes(parent_id, keyword, scope_paths)` — Resolve subtree, return notes
- `post(note)` — Append note, trigger activity counter reset

**Active Mode:**
- `on_edit(task_id, file_path)` — Track edit activity
- `on_posthook(task_id)` — Reset turn counter
- `tick(task_id)` — Increment turn counter
- `should_checkpoint(task_id)` — Check thresholds, return "edit" or "turn" or None
- `check(task_id, snapshot, api_client, model)` — Spawn external-trigger agent if thresholds crossed

---

## Files Involved

**Core:**
- `backend/src/team/task_center.py` — Unified TaskCenter
- `backend/src/team/runtime/dispatch_queue.py` — Thin queue extraction
- `backend/src/team/persistence/task_store.py` — SQL persistence delegation
- `backend/src/team/models.py` — Task/Plan/Blocker data classes

**Supporting:**
- `backend/src/team/note_manager.py` — Note storage and querying
- `backend/src/team/activity_tracker.py` — Edit/turn counter tracking
- `backend/src/team/checkpoint_manager.py` — Pause checkpoint rehydration
- `backend/src/team/runtime/conductor.py` — Blocker execution
- `backend/src/team/runtime/executor.py` — Task dispatch loop

**Date:** 2026-04-14
