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

```mermaid
classDiagram
    class TaskCenter {
        -_store: TaskStore
        -_notes: list[Note]
        -_activity_counters: dict
        -_team_run_id: str
        +post(note: Note)
        +read(authors, scope_paths, keyword)
        +context_for(task) → str
        +read_sibling_notes(parent_id) → str
        +mark_running(task_id, agent_run_id) → Task
        +complete_task(task_id, result) → list[Task]
        +fail(task_id, reason)
        +pause_running_task(task_id, blocker_id, verdict)
        +resume_paused_tasks(blocker_id) → int
        +cancel_paused_tasks(blocker_id) → int
        +request_replan(task_id, request) → Task
        +apply_replan(replan_id, add_tasks, cancel_ids)
        +on_edit(task_id, file_path)
        +on_posthook(task_id)
        +tick(task_id)
        +should_checkpoint(task_id) → str|None
        +check(task_id, snapshot, api_client, model) → bool
    }

    class DispatchQueue {
        -session_factory
        +pop_ready(run_id) → TaskRecord|None
    }

    class TaskStore {
        -session_factory
        -team_run_id
        +insert_plan(specs, parent_id, parent_depth, parent_root_id)
        +get_task(task_id) → Task|None
        +get_all_tasks() → list[TaskRecord]
        +get_adjacency() → dict
        +_mark_done(task_id, result)
        +_fail_task_sql(task_id, reason, cascade_policy)
        +_pause_running_task_sql(task_id, blocker_id, checkpoint)
        +_resume_paused_tasks_sql(blocker_id)
        +_cancel_by_ids_sql(task_ids)
    }

    class Note {
        task_id: str
        agent_name: str
        content: str
        timestamp: float
        scope_paths: list[str]
    }

    class Conductor {
        -_team_run
        -_executor_snapshots
        +create_blocker(initiating_task_id, reason, root_cause_paths)
    }

    class Executor {
        -task_center
        -dispatch_queue
        -conductor
        +_run_one(task)
        +_dispatch(task, result)
    }

    TaskCenter --> TaskStore: delegates SQL
    TaskCenter --> Note: owns notes
    DispatchQueue ..> TaskCenter: reads task state
    Conductor --> TaskCenter: pause/resume tasks, post notes
    Executor --> TaskCenter: mark_running, context_for, complete_task
    Executor --> DispatchQueue: pop_ready
```

---

## Task Lifecycle States

```mermaid
stateDiagram-v2
    [*] --> PENDING: inserted in plan
    
    PENDING --> READY: dependencies satisfied
    READY --> RUNNING: pop_ready claimed by executor
    
    RUNNING --> DONE: execution succeeded
    RUNNING --> FAILED: execution failed
    RUNNING --> EXPANDED: agent submitted child plan
    RUNNING --> PAUSED: blocker assessment YES
    
    EXPANDED --> DONE: all children completed
    
    PAUSED --> READY: blocker resolved, resume_paused_tasks()
    PAUSED --> CANCELLED: blocker fix failed
    
    READY --> CANCELLED: cascade from parent or cancel_by_ids()
    PENDING --> CANCELLED: cascade from parent or cancel_by_ids()
    FAILED --> [*]
    DONE --> [*]
    CANCELLED --> [*]
    
    note right of PAUSED
        Only RUNNING tasks can pause.
        READY/PENDING tasks unaffected during blocker.
        Parent stays EXPANDED while any child is PAUSED.
    end note
    
    note right of EXPANDED
        Agent submitted a plan.
        TaskCenter waits for children to complete.
        If result.submitted_plan exists, insert_plan() is called.
    end note
```

---

## Context Building for Agents

```mermaid
flowchart TD
    A["task_center.context_for(task, max_context_bytes)"] --> B{What to include?}
    
    B -->|Retry context| C["If task.retry_count > 0:<br/>Previous failure reason"]
    B -->|Task description| D["task.instruction"]
    B -->|Self notes| E["Read all notes from this task"]
    B -->|Dependency notes| F["Walk task.dependencies,<br/>read notes from each dep"]
    B -->|File changes| G["FileChangeStore:<br/>Files edited since task creation"]
    B -->|Parent chain| H["Walk ancestor chain,<br/>read notes from each parent"]
    
    C --> Z["Build priority-ordered context string<br/>respecting max_context_bytes"]
    D --> Z
    E --> Z
    F --> Z
    G --> Z
    H --> Z
    
    Z --> RESULT["Return context: str"]
    
    style A fill:#e1f5ff
    style RESULT fill:#c8e6c9
```

---

## Blocker Protocol Lifecycle

The blocker protocol detects when a systemic failure affects multiple siblings and coordinates a single fix before resuming.

```mermaid
sequenceDiagram
    participant Agent as Running Agent
    participant TC as TaskCenter
    participant Replanner as Replanner Agent
    participant Conductor as Conductor
    participant Resolver as Resolver Agent
    participant ResumedAgent as Resumed Agents
    
    Agent->>TC: request_replan(task_id, reason)
    Note over TC: Mark task FAILED,<br/>spawn replanner task
    
    Replanner->>TC: read_sibling_notes(parent_id)
    Note over Replanner: Assess: are failures<br/>from shared root cause?
    
    Replanner->>TC: declare_blocker(reason, root_cause_paths)
    Note over TC: Record blocker,<br/>status=ASSESSING
    
    Conductor->>TC: get_siblings_and_descendants(task_id)
    Note over Conductor: Determine scope:<br/>all siblings + their subtrees
    
    loop For each RUNNING sibling
        Conductor->>Conductor: assess_pause(task_snapshot)
        Note over Conductor: External trigger agent<br/>evaluates if paused by blocker
        Conductor->>TC: pause_running_task(task_id, blocker_id, verdict)
        Note over TC: Mark task PAUSED,<br/>store checkpoint, verdict
    end
    
    Conductor->>Resolver: spawn_resolver(root_cause_paths, blocker_id)
    Note over Resolver: Fix task: repair broken files
    
    Resolver->>TC: complete_task(fix_task_id, result)
    Note over TC: Mark blocker RESOLVED
    
    Conductor->>TC: resume_paused_tasks(blocker_id)
    Note over TC: Transition PAUSED → READY
    
    ResumedAgent->>TC: mark_running(task_id, agent_run_id)
    Note over ResumedAgent: Rehydrate checkpoint<br/>from pause_checkpoint field,<br/>continue from where paused
```

---

## Active Mode Auto-Note Generation

Active mode spawns external-trigger agents to post notes on behalf of silent agents, ensuring blockers are surfaced early.

```mermaid
sequenceDiagram
    participant Executor as Executor
    participant TC as TaskCenter
    participant ExtTrig as External Trigger
    participant AutoAgent as Auto-Note Agent
    participant TC2 as TaskCenter<br/>post()
    
    Executor->>TC: on_edit(task_id, file_path)
    Note over TC: Increment edits_since_note
    
    Executor->>TC: on_posthook(task_id)
    Note over TC: Reset turns_since_posthook to 0
    
    loop Every turn
        Executor->>TC: tick(task_id)
        Note over TC: Increment turns_since_posthook
    end
    
    Executor->>TC: check(task_id, snapshot, api_client, model)
    
    alt edits_since_note >= 5
        Note over TC: EDIT threshold crossed
        TC->>ExtTrig: run_checkpoint_note(prompt="EDIT_CHECKPOINT_PROMPT")
    else turns_since_posthook >= 10
        Note over TC: TURN threshold crossed
        TC->>ExtTrig: run_checkpoint_note(prompt="TURN_CHECKPOINT_PROMPT")
    else
        Note over TC: Neither threshold crossed
        TC-->>Executor: return False
    end
    
    ExtTrig->>AutoAgent: Create ephemeral agent<br/>with snapshot + PostNoteTool
    Note over AutoAgent: tool_choice="any", retry until success
    
    AutoAgent->>TC2: post_note(content)
    Note over TC2: Post note under original task,<br/>agent_name = "name (auto)"
    
    TC2-->>TC: on_note_posted(note)
    Note over TC: Reset both counters to 0,<br/>ignore system notes
    
    TC-->>Executor: return True
    
    Note over ExtTrig: Turn prompt explicitly<br/>asks: "Is the agent blocked<br/>by code another task broke?"<br/>Surfaces blockers early.
```

---

## Blocker Assessment — Determining Pause Verdicts

```mermaid
flowchart TD
    A["Conductor._assess_running(blocker_id)"] --> B["Get scope:<br/>get_siblings_and_descendants()"]
    B --> C["Filter RUNNING tasks only"]
    C --> D["For each RUNNING task"]
    
    D --> E["Spawn external_trigger agent"]
    E --> F["Inputs:<br/>- task snapshot<br/>- running agent's messages<br/>- blocker reason + root_cause_paths"]
    
    F --> G["PauseVerdictTool:<br/>Yes/No/Unclear"]
    
    G -->|Yes| H["pause_running_task()<br/>status=PAUSED"]
    G -->|No| I["Leave status=RUNNING<br/>Task can continue"]
    G -->|Unclear| J["Leave status=RUNNING<br/>May fail naturally,<br/>trigger request_replan,<br/>replanner sees blocker context"]
    
    H --> K["Store: blocker_id,<br/>pause_checkpoint,<br/>verdict"]
    I --> L["Task continues normally"]
    J --> L
    
    style A fill:#e1f5ff
    style H fill:#ffccbc
    style I fill:#c8e6c9
    style J fill:#fff9c4
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
