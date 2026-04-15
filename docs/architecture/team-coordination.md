# Team Coordination

EphemeralOS team coordination separates work execution, failure recovery, and blocker mechanics across three strict roles: **Developer** (reports failure), **Replanner** (LLM-driven decision), and **Conductor** (deterministic mechanics). This document illustrates the complete workflow using Mermaid diagrams.

---

## Three Roles with Strict Separation

```
┌─────────────────────────────────┐   ┌─────────────────────────────────┐   ┌─────────────────────────────────┐
│   Developer (Work Agent)        │   │   Replanner (LLM Agent)         │   │   Conductor (Deterministic)     │
│                                 │   │                                 │   │                                 │
│  ┌───────────────────────┐      │   │  ┌───────────────────────┐      │   │  ┌───────────────────────┐      │
│  │   Executes task       │      │   │  │  Reads failure context│      │   │  │ Executes blocker      │      │
│  └───────────┬───────────┘      │   │  └───────────┬───────────┘      │   │  │ mechanics             │      │
│              │                  │   │              │                  │   │  └───────────┬───────────┘      │
│  ┌───────────▼───────────┐      │   │  ┌───────────▼───────────┐      │   │              │                  │
│  │ Posts notes to        │      │   │  │ Analyzes sibling notes│      │   │  ┌───────────▼───────────┐      │
│  │ TaskCenter            │      │   │  └───────────┬───────────┘      │   │  │ Spawns external       │      │
│  └───────────┬───────────┘      │   │              │                  │   │  │ triggers              │      │
│              │                  │   │  ┌───────────▼───────────┐      │   │  └───────────┬───────────┘      │
│  ┌───────────▼───────────┐      │   │  │  Assesses plan health │      │   │              │                  │
│  │ Calls request_replan  │      │   │  └───────────┬───────────┘      │   │  ┌───────────▼───────────┐      │
│  │ on failure            │      │   │              │                  │   │  │ Manages pause/resume  │      │
│  │ (no blocker awareness)│      │   │  ┌───────────▼───────────┐      │   │  └───────────┬───────────┘      │
│  └───────────┬───────────┘      │   │  │ Decides: 3 actions    │      │   │              │                  │
│              │                  │   │  │ only                  │      │   │  ┌───────────▼───────────┐      │
└──────────────┼──────────────────┘   │  └───────────┬───────────┘      │   │  │ Spawns resolver task  │      │
               │                      │              │                  │   │  │ (zero LLM calls)      │      │
               │  request_replan      └──────────────┼──────────────────┘   │  └───────────┬───────────┘      │
               └─────────────────────────────────────▶                      └──────────────┼──────────────────┘
                                                      │  declare_blocker                   │
                                                      └────────────────────────────────────▶
                                                                                            │ resume
                                                                                            ▼
                                                                            ┌───────────────────────────────┐
                                                                            │  TaskCenter                   │
                                                                            │  (Unified Task Lifecycle)     │
                                                                            └───────────────────────────────┘
```

---

## Plan & Dispatch

```
  Planner            TaskCenter           DispatchQueue         Developer
  (LLM Agent)        (Unified Log)        (Pop Ready)           (Work Agent)
      │                   │                    │                     │
      │ submit_plan(       │                    │                     │
      │  tasks=[...])      │                    │                     │
      │──────────────────▶│                    │                     │
      │                   │ Validate & insert  │                     │
      │                   │ TaskSpecs into DAG │                     │
      │                   │                    │                     │
      │◀ ─ ─ ─ ─ ─ ─ ─ ─ ─│                    │                     │
      │  plan submitted   │                    │                     │
      │                   │         Ready queue monitors             │
      │                   │         dependencies                     │
      │                   │                    │                     │
      │                   │                    │◀────────────────────│
      │                   │                    │  pop_ready()        │
      │                   │                    │                     │
      │                   │                    │────────────────────▶│
      │                   │                    │  Task               │
      │                   │                    │                     │
      │                   │◀────────────────────────────────────────│
      │                   │  context_for(task) │                     │
      │                   │  deps+parent+notes │                     │
      │                   │                    │                     │
      │                   │─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─▶│
      │                   │  prioritized context                     │
      │                   │                    │                     │
      │                   │                    │         ┌───────────┴───────────┐
      │                   │                    │         │ query.py loop         │
      │                   │                    │         │ (work, tools, notes)  │
      │                   │                    │         │ reads code, edits     │
      │                   │                    │         │ files, posts progress │
      │                   │                    │         └───────────┬───────────┘
      │                   │                    │                     │
      │                   │                    │         ┌───────────┴───────────┐
      │                   │                    │         │ Terminal submission   │
      │                   │                    │         │ via main loop tool   │
      │                   │                    │         └───────────┬───────────┘
      │                   │                    │                     │
      │                   │◀────────────────────────────────────────│
      │                   │  submit_summary | submit_plan            │
      │                   │  | request_retry | request_replan        │
      │                   │                    │                     │
      │                   │─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─▶│
      │                   │  submission confirmed                    │
```

---

## Replan on Failure: Three Decision Branches

When a Developer calls `request_replan()`, the Replanner reads failure context and sibling notes, then decides one of three actions.

**Key change:** The failed task enters `REPLANNING` status (non-terminal). Its dependents stay `PENDING` — they are NOT cascade-cancelled. When the replan succeeds, dependents are rewired to the new replacement tasks.

```
  Failed             TaskCenter           Replanner            Conductor
  Developer          (Notes & Context)    (LLM)                (Mechanics)
      │                   │                   │                    │
      │ request_replan(   │                   │                    │
      │  reason,          │                   │                    │
      │  suggestion)      │                   │                    │
      │──────────────────▶│                   │                    │
      │                   │ Mark task          │                    │
      │                   │ REPLANNING        │                    │
      │                   │ (non-terminal)    │                    │
      │                   │ dependents stay   │                    │
      │                   │ PENDING           │                    │
      │                   │ Spawn Replanner   │                    │
      │                   │ (fired_by=X.id)  │                    │
      │                   │──────────────────▶│                    │
      │                   │                   │                    │
      │                   │◀──────────────────│                    │
      │                   │ context_for(      │                    │
      │                   │  replanner_task)  │                    │
      │                   │                   │                    │
      │                   │──────────────────▶│                    │
      │                   │ failure reason +  │                    │
      │                   │ siblings +        │                    │
      │                   │ completed notes   │                    │
      │                   │                   │                    │
      │         ┌─────────┴──────────┐         │                    │
      │         │ Assess failure      │         │                    │
      │         │ pattern: 3 scenarios│         │                    │
      │         └─────────┬──────────┘         │                    │
      │                   │                    │                    │
      │         ┌─────────┴──────────────────┐│                    │
      │         │ Branch 1: add_tasks         ││                    │
      │         │ "Plan has a gap. Missing    ││                    │
      │         │  work or retry needed."     ││                    │
      │         └─────────┬──────────────────┘│                    │
      │                   │ submit_replan(     │                    │
      │                   │  add_tasks=[...]) │                    │
      │                   │◀──────────────────│                    │
      │                   │ Insert new tasks   │                    │
      │                   │ REWIRE dependents  │                    │
      │                   │ to new tasks      │                    │
      │                   │ Mark original      │                    │
      │                   │ REPLANNING → FAILED│                    │
      │                   │                   │                    │
      │         ┌─────────┴──────────────────┐│                    │
      │         │ Branch 2: declare_blocker   ││                    │
      │         │ "Shared dependency broken.  ││                    │
      │         │  Multiple siblings will     ││                    │
      │         │  fail same way."            ││                    │
      │         └─────────┬──────────────────┘│                    │
      │                   │                   │ declare_blocker(   │
      │                   │                   │  paths, reason)    │
      │                   │                   │───────────────────▶│
      │                   │ Original stays    │                    │ Pause RUNNING siblings
      │                   │ REPLANNING        │                    │ Spawn resolver
      │                   │                   │                    │ Resume after fix
      │                   │                   │                    │ → post-fix replanner
      │                   │                   │                    │   targets original
      │         ┌─────────┴──────────────────┐│                    │
      │         │ Branch 3: cancel_and_redraft││                    │
      │         │ "Plan fundamentally wrong.  ││                    │
      │         │  Restart from scratch."     ││                    │
      │         └─────────┬──────────────────┘│                    │
      │                   │ submit_replan(     │                    │
      │                   │  cancel_ids=[...], │                    │
      │                   │  add_tasks=[new])  │                    │
      │                   │◀──────────────────│                    │
      │                   │ Cancel siblings    │                    │
      │                   │ REWIRE dependents │                    │
      │                   │ to new tasks      │                    │
      │                   │ Mark original     │                    │
      │                   │ REPLANNING → FAILED│                   │
      │                   │                   │                    │
      │                   │──────────────────▶│                    │
      │                   │ replan confirmed   │                    │
```

---

## Blocker Lifecycle: Pause → Fix → Resume

When the Replanner declares a blocker, the Conductor mechanically executes the pause/fix/resume sequence:

```
  Replanner    Conductor         ExternalTrigger    RUNNING Agents    Resolver      TaskCenter
               (Deterministic)   (PauseAssessment)  (Paused)          (Fix Task)
      │              │                  │                 │                │              │
      │ declare_     │                  │                 │                │              │
      │ blocker(     │                  │                 │                │              │
      │  root_cause_ │                  │                 │                │              │
      │  paths,      │                  │                 │                │              │
      │  reason)     │                  │                 │                │              │
      │─────────────▶│                  │                 │                │              │
      │              │                  │                 │                │              │
      │              │  ASSESSING Phase │                 │                │              │
      │              │  ─────────────── │                 │                │              │
      │              │  Identify RUNNING│                 │                │              │
      │              │  siblings +      │                 │                │              │
      │              │  descendants     │                 │                │              │
      │              │                  │                 │                │              │
      │              │ assess_pause per │                 │                │              │
      │              │ RUNNING agent    │                 │                │              │
      │              │ (parallel)       │                 │                │              │
      │              │─────────────────▶│                 │                │              │
      │              │                  │ "Does your task │                │              │
      │              │                  │  depend on      │                │              │
      │              │                  │  {broken_files}"│                │              │
      │              │◀─────────────────│                 │                │              │
      │              │ PauseVerdict:    │                 │                │              │
      │              │ YES / NO         │                 │                │              │
      │              │                  │                 │                │              │
      │              │── Verdict YES ──▶│                 │                │              │
      │              │  terminate +     │                 │                │              │
      │              │  save checkpoint │                 │                │              │
      │              │─────────────────────────────────▶│                │              │
      │              │                  │                 │ mark PAUSED    │              │
      │              │                  │                 │ (blocker_id,   │              │
      │              │                  │                 │  pause_        │              │
      │              │                  │                 │  checkpoint)   │              │
      │              │                  │                 │───────────────────────────▶│
      │              │                  │                 │                │              │
      │              │── Verdict NO ───▶│                 │                │              │
      │              │  discard, agent  │                 │                │              │
      │              │  continues       │                 │                │              │
      │              │                  │                 │                │              │
      │              │  FIXING Phase    │                 │                │              │
      │              │  ─────────────── │                 │                │              │
      │              │  All assessments │                 │                │              │
      │              │  resolved        │                 │                │              │
      │              │                  │                 │                │              │
      │              │ spawn fix task   │                 │                │              │
      │              │ (depth=0)        │                 │                │              │
      │              │─────────────────────────────────────────────────▶│              │
      │              │                  │                 │     ┌──────────┴─────────┐   │
      │              │                  │                 │     │ main loop          │   │
      │              │                  │                 │     │ (repair shared     │   │
      │              │                  │                 │     │  surface)          │   │
      │              │                  │                 │     └──────────┬─────────┘   │
      │              │                  │                 │                │              │
      │              │                  │     Fix succeeds:               │              │
      │              │                  │                 │     post_note( │              │
      │              │                  │                 │      fix summary)             │
      │              │                  │                 │                │─────────────▶│
      │              │◀─────────────────────────────────────────────────│              │
      │              │ on_fix_complete() │                 │                │              │
      │              │                  │                 │                │              │
      │              │                  │     Fix fails:  │                │              │
      │              │                  │                 │ request_replan(│              │
      │              │                  │                 │  concrete      │              │
      │              │                  │                 │  reason)       │              │
      │              │                  │                 │                │─────────────▶│
      │              │◀─────────────────────────────────────────────────│              │
      │              │ on_fix_failed()  │                 │                │              │
      │              │ Cancel PAUSED    │                 │                │              │
      │              │ tasks / Fail run │                 │                │              │
      │              │                  │                 │                │              │
      │              │  RESOLVED Phase  │                 │                │              │
      │              │  ─────────────── │                 │                │              │
      │              │                  │                 │                │              │
      │              │ resume_paused_   │                 │                │              │
      │              │ tasks(blocker_id)│                 │                │              │
      │              │ mark READY from  │                 │                │              │
      │              │ PAUSED           │                 │                │              │
      │              │─────────────────────────────────▶│                │              │
      │              │                  │     Load pause_checkpoint       │              │
      │              │                  │     Inject fix summary          │              │
      │              │                  │     Spawn new agent run         │              │
      │              │                  │                 │                │              │
      │              │                  │     Resume from checkpoint      │              │
      │              │                  │     + fix context               │              │
      │              │                  │                 │                │              │
      │ spawn post-fix│                 │                 │                │              │
      │ replanner for│                  │                 │                │              │
      │ initiating   │                  │                 │                │              │
      │ task         │                  │                 │                │              │
      │◀─────────────│                  │                 │                │              │
```

---

## Task Status Transitions with Blocker

```
                        ┌─────────┐
           task created │         │
         ┌──────────────▶ PENDING │
         │               │         │
         │               └────┬────┘
         │                    │ deps satisfied
         │               ┌────▼────┐
         │               │         │
         │               │  READY  │
         │               │         │
         │               └────┬────┘
         │                    │ pop_ready
         │               ┌────▼────┐
         │               │         │◀────────────────────────────────────────────┐
         │               │ RUNNING │                                              │
         │               │         │                                              │
         │               └──┬──┬───┘                                             │
         │                  │  │  │  \                                            │
         │    completes      │  │  │   \ Conductor                                │
         │    successfully   │  │  │    \ assess YES                              │
         │   ┌───────────────┘  │  │     \ (blocker pause)                       │
         │   │                  │  │      ▼                                       │
         │   │    fails         │  │   ┌──────┐    blocker resolved               │
         │   │    (no retry)    │  │   │      │    (_resume_paused)               │
         │   │  ┌───────────────┘  │   │PAUSED├─────────────────────────────────┘
         │   │  │                  │   │      │
         │   │  │    submits plan  │   └──────┘
         │   │  │   ┌─────────────┘      Non-terminal. RUNNING agents only.
         │   │  │   │                    Checkpoint saved. Resume from here.
         │   │  │   │
         │   │  │   │ request_replan
         │   │  │   ▼
         │   │  │ ┌───────────┐
         │   │  │ │REPLANNING│◀─── Non-terminal. Dependents stay PENDING.
         │   │  │ └───┬─────┘
         │   │  │     │ replan succeeds / fails
         │   │  │     ▼
         ▼   ▼  ▼ ┌───────┐
       ┌────┐ ┌───────┐ │FAILED│
       │DONE│ │FAILED │ └───┬─┘
       └──┬─┘ └───┬───┘     │
          │       │    dependents │
          │       │    rewired   │
          │       │     ┌──────▼──────┐
          │       │     │ Dependents   │
          │       │     │ now point to │
          │       │     │ replacements │
          │       │     └──────┬──────┘
          │       │            │
          │       │     ┌─────▼─────┐
          │       │     │  (tasks)  │
          │       │     └───────────┘
          ▼       ▼
       ┌────┐ ┌───────┐ ┌──────────┐   ┌───────────┐
       │DONE│ │FAILED │ │ EXPANDED │   │ CANCELLED │
       └──┬─┘ └───┬───┘ └────┬─────┘   └─────┬─────┘
          │       │     children│             │
          │       │     complete│             │
          │       │      ┌──────▼──────┐      │
          │       │      │    DONE     │      │
          │       │      └──────┬──────┘      │
          │       │             │             │
          ▼       ▼             ▼             ▼
         [*]     [*]           [*]           [*]

  Note: READY/PENDING tasks during a blocker stay unchanged — free to dispatch.
  Note: REPLANNING is non-terminal; dependents are NOT cascade-cancelled.
```
                        ┌─────────┐
           task created │         │
        ┌───────────────▶ PENDING │
        │               │         │
        │               └────┬────┘
        │                    │ deps satisfied
        │               ┌────▼────┐
        │               │         │
        │               │  READY  │
        │               │         │
        │               └────┬────┘
        │                    │ pop_ready
        │               ┌────▼────┐
        │               │         │◀────────────────────────────────────────────┐
        │               │ RUNNING │                                              │
        │               │         │                                              │
        │               └──┬──┬───┘                                             │
        │                  │  │  │  \                                            │
        │    completes      │  │  │   \ Conductor                                │
        │    successfully   │  │  │    \ assess YES                              │
        │   ┌───────────────┘  │  │     \ (blocker pause)                       │
        │   │                  │  │      ▼                                       │
        │   │    fails         │  │   ┌──────┐    blocker resolved               │
        │   │    (no retry)    │  │   │      │    (_resume_paused)               │
        │   │  ┌───────────────┘  │   │PAUSED├─────────────────────────────────┘
        │   │  │                  │   │      │
        │   │  │    submits plan  │   └──────┘
        │   │  │   ┌─────────────┘      Non-terminal. RUNNING agents only.
        │   │  │   │                    Checkpoint saved. Resume from here.
        │   │  │   │
        ▼   ▼  ▼   ▼
      ┌────┐ ┌───────┐ ┌──────────┐   ┌───────────┐
      │DONE│ │FAILED │ │ EXPANDED │   │ CANCELLED │
      └──┬─┘ └───┬───┘ └────┬─────┘   └─────┬─────┘
         │       │     children│             │
         │       │     complete│             │
         │       │      ┌──────▼──────┐      │
         │       │      │    DONE     │      │
         │       │      └──────┬──────┘      │
         │       │             │             │
         ▼       ▼             ▼             ▼
        [*]     [*]           [*]           [*]

  Note: READY/PENDING tasks during a blocker stay unchanged — free to dispatch.
```

---

## Resume & Rehydration

When a PAUSED task transitions back to READY, the Executor resumes from the saved checkpoint with additional context about the fix:

```
┌──────────────────────────────┐        ┌──────────────────────────────┐        ┌──────────────────────────────┐
│       PAUSED State           │        │        Resume Flow            │        │       Resumed Agent          │
│                              │        │                               │        │                              │
│  ┌────────────────────────┐  │        │  ┌─────────────────────────┐ │        │  ┌────────────────────────┐  │
│  │   Agent cancelled      │  │        │  │ TaskCenter transitions   │ │        │  │ Sees full prior        │  │
│  └───────────┬────────────┘  │        │  │ PAUSED → READY          │ │        │  │ conversation           │  │
│              │               │        │  └────────────┬────────────┘ │        │  └───────────┬────────────┘  │
│  ┌───────────▼────────────┐  │        │               │              │        │              │               │
│  │ Conversation snapshot  │  │        │  ┌────────────▼────────────┐ │        │  ┌───────────▼────────────┐  │
│  │ saved in               │  │        │  │ Executor loads          │ │        │  │ Sees fix context       │  │
│  │ pause_checkpoint       │  │        │  │ pause_checkpoint        │ │        │  └───────────┬────────────┘  │
│  └───────────┬────────────┘  │        │  └────────────┬────────────┘ │        │              │               │
│              │               │        │               │              │        │  ┌───────────▼────────────┐  │
│  ┌───────────▼────────────┐  │        │  ┌────────────▼────────────┐ │        │  │ Continues normally     │  │
│  │ blocker_id tracked     │  │        │  │ Inject fix message:     │ │        │  │ from that point        │  │
│  └────────────────────────┘  │        │  │ reason + fix_summary    │ │        │  └────────────────────────┘  │
│                              │        │  └────────────┬────────────┘ │        │                              │
└──────────────┬───────────────┘        │               │              │        └──────────────▲───────────────┘
               │                        │  ┌────────────▼────────────┐ │                       │
               │                        │  │ Spawn new agent run     │ │                       │
               │                        │  │ from frozen checkpoint  │ │                       │
               │                        │  └────────────────────────┘ │                       │
               │                        │                              │                       │
               └───────────────────────▶└──────────────────────────────┘──────────────────────┘
```

---

## Key Design Principles

**Strict Role Separation**
- Developer knows nothing about blockers; only calls `request_replan()` on failure.
- Replanner makes all failure recovery decisions; has 3 actions only: `add_tasks`, `declare_blocker`, `cancel_and_redraft`.
- Conductor executes mechanics deterministically; zero LLM calls, fully testable.

**No Dispatch Guard**
- Tasks dispatch freely during active blockers.
- READY/PENDING tasks are never touched by the blocker protocol.
- Non-RUNNING tasks continue normally; if they hit the broken dependency, they fail and trigger their own `request_replan()`.

**Dependent Rewiring on Replan**
- When a task enters REPLANNING, its dependents stay PENDING (not cascade-cancelled).
- The replanner's outcome (add_tasks, cancel_and_redraft) determines how dependents get rewired.
- Replanner is spawned with `fired_by_task_id` pointing to the original task.
- If replanner produces tasks: dependents are rewired from original to new tasks.
- If replanner declares blocker: original stays REPLANNING, post-fix replanner inherits `fired_by_task_id`.
- If replanner fails: original is marked FAILED with cascade.

**Parallel Safety**
- Only RUNNING agents are assessed for pause.
- Multiple blockers with overlapping paths merge into one.
- Assessment scope is structural (siblings + descendants), not file-path based.

**Checkpoint-Based Resume**
- Paused task's full conversation snapshot is preserved.
- Fix summary injected as context, not as a tool call.
- Resumed agent picks up where it left off with full prior context.

**Guaranteed Submission**
- Every task exits via terminal submission tools in the main loop, which always complete.
- Terminal tools: `submit_task_plan`, `submit_task_summary`, `declare_blocker`.
- The executor reads structured metadata from tool results and dispatches to TaskCenter.
