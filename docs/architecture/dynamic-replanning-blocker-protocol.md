# Dynamic Replanning: Blocker-Aware Pause/Fix/Resume Protocol

**Status:** IMPLEMENTED  
**Date:** 2026-04-14  
**Branch:** `codex/pydantic-benchmark-loop`  
**Author:** Architecture session  
**Depends on:** Plan A Team Coordination Redesign (plan-a-team-coordination-redesign.md)  
**Prerequisite:** TaskCenter + DAG Unification (task-center-dag-unification.md)  
**Companion:** TaskCenter Active Mode (task-center-active-mode.md)

---

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [Design Goals](#2-design-goals)
3. [Architecture Overview](#3-architecture-overview)
4. [Task Status — Extended State Machine](#4-task-status--extended-state-machine)
5. [Data Model](#5-data-model)
6. [Replanner Decision Tree](#6-replanner-decision-tree)
7. [Blocker Lifecycle](#7-blocker-lifecycle)
8. [External Trigger Module](#8-ephemeraltask-module)
9. [Conductor](#9-conductor)
10. [Toolkit Changes](#10-toolkit-changes)
11. [TaskCenter Changes](#11-taskcenter-changes-formerly-dispatcher)
12. [Resume Protocol](#12-resume-protocol)
13. [Task Center Integration](#13-task-center-integration)
14. [Scope and Boundaries](#14-scope-and-boundaries)
15. [Budget and Safety](#15-budget-and-safety)
16. [Walkthrough — The compatibility.py Scenario](#16-walkthrough--the-compatibilitypy-scenario)
17. [Implementation Snapshot](#17-implementation-snapshot)

---

## 1. Problem Statement

When a completed task breaks a shared dependency mid-run, sibling tasks that depend on that shared code fail independently. The current system handles each failure in isolation — each task retries, each retry fails again, each exhaustion triggers a separate replan. There is no mechanism to detect "these failures share a root cause" and coordinate a single fix before resuming.

### The Scenario

A planner decomposes a dask bug-fix run into 32 HDF tasks and a compatibility.py refactor task. The planner does NOT declare dependencies between them (a planning gap). The refactor task completes first and breaks `dask/compatibility.py` by replacing `_EMSCRIPTEN` with a `__getattr__` mechanism that removes the `parse` import. All 32 HDF tasks then fail with the same `ImportError` because they all import `compatibility.py` transitively.

Without coordination: 32 independent failures, 32 retries, 32 re-failures, multiple replanners racing to fix the same file.

With this protocol: the replanner detects the systemic pattern, declares a blocker, pauses all siblings, fixes the root cause once, and resumes everyone.

---

## 2. Design Goals

| # | Goal | How |
|---|------|-----|
| G-1 | Developer stays simple | Developer has 2 tools: `post_note` and `request_replan`. No blocker awareness. |
| G-2 | Single decision point | Replanner owns all failure recovery decisions. Three clear actions, zero overlap. |
| G-3 | Conductor is deterministic | Conductor executes blocker mechanics. No LLM calls. Fully testable. |
| G-4 | query.py untouched | Blocker protocol operates outside the query loop. No injection, no halt directives, no buffer flushes. |
| G-5 | Sibling scoped | Blocker assesses all siblings of the initiating task plus their subtree children. No cross-subtree coordination. Simple and predictable. |
| G-6 | Zero impact on unaffected agents | External-trigger-based assessment means agents that are not affected never see the blocker notification. |
| G-7 | Non-running tasks untouched | Only RUNNING agents are assessed and potentially paused. READY/PENDING/FAILED tasks keep their status unchanged. |
| G-8 | Replanner sees active blockers | Replanner context includes active blocker info so it can merge related failures into existing blockers rather than creating duplicates. |
| G-9 | Durable blocker state | Blockers are persisted to the database and restored on crash/restart. |

---

## 3. Architecture Overview

### Role Responsibility Map

    Developer       "I failed"                      reports failure
         |                                          (request_replan)
         v
    Replanner       "Here's what to do"             assesses and decides
         |                                          (add_tasks / declare_blocker /
         |                                           cancel_and_redraft)
         v
    Conductor       "Executing blocker protocol"    mechanical execution
         |                                          (pause, assess, terminate,
         |                                           fix, resume)
         v
    Resolver        "Repairing root cause"          developer agent (via find_by_role)
         |                                          (scoped to broken files,
         |                                           normal developer posthook tools)
         v
    Conductor       "Post-fix coordination"         resume assessed agents,
         |                                          spawn replanner for initiator
         v
    Resumed Agents  "Continuing from checkpoint"    resume from external trigger checkpoint state

### System-Level Flow

    Developer fails
          |
          | request_replan()
          v
    TaskCenter: mark task FAILED, spawn replanner
                (siblings UNTOUCHED — no auto-cancel)
          |
          v
    Replanner reads context: failure reason, sibling statuses,
    sibling notes, plan health, children statuses
          |
          v
    Replanner decides:
          |
          +---> add_tasks -----------> TaskCenter inserts new tasks
          |                            siblings untouched
          |
          +---> declare_blocker -----> Conductor executes:
          |                            assess RUNNING agents only
          |                            (non-running tasks untouched)
          |                            guard pop_ready during fix
          |                            spawn resolver (dedicated role)
          |                            on fix: resume assessed agents,
          |                              lift guard, spawn replanner
          |                              for initiator
          |                            on fix fail: mark team run FAILED
          |
          +---> cancel_and_redraft --> TaskCenter cancels all
                                       siblings + children,
                                       inserts new plan

---

## 4. Task Status — Extended State Machine

### Status Values

    PENDING     waiting for dependencies to complete
    READY       dependencies satisfied, eligible for dispatch
    RUNNING     actively being executed by an agent
    EXPANDED    planner submitted child tasks, waiting for them
    PAUSED      stopped by Conductor (RUNNING agents only),       <-- NEW
                waiting for blocker fix
    DONE        completed successfully
    FAILED      execution failed
    CANCELLED   cancelled by cascade or replan

### Terminal vs Non-Terminal

    Non-terminal:  PENDING, READY, RUNNING, EXPANDED, PAUSED
    Terminal:      DONE, FAILED, CANCELLED

PAUSED is non-terminal. This is the critical property: a parent with a PAUSED child stays EXPANDED. The maybe_promote_expanded_parent function will not promote the parent to DONE while any child is PAUSED. No ancestor chain reopening is needed in the common case.

Only RUNNING tasks can transition to PAUSED (via pause assessment YES verdict). Non-running tasks (READY, PENDING, FAILED) are never touched by the blocker protocol. READY tasks continue to dispatch normally — if they hit the broken dependency, they fail and trigger their own request_replan, where the replanner sees the existing blocker context via sibling notes.

### Transition Diagram

                   deps met
    PENDING -----------------------> READY
       |                               |
       |                               | pop_ready
       |                               v
       |                            RUNNING
       |                               |
       |                          +----+--------+--------+
       |                          |    |        |        |
       |                          v    v        v        v
       |                        DONE  FAILED  EXPANDED  (agent submits plan)
       |                                                    |
       |          Conductor                                 | children
       |          Authority                                 | complete
       |                                                    v
       |                                                  DONE
       |
       |                          blocker
       |                          created
       |                             |
       |                    PAUSED <--- RUNNING (via pause assessment+terminate)
       |                       |
       |                       | blocker resolved
       |                       v
       |                    READY  (re-enters normal flow, new agent run
       |                            from pause_checkpoint)
       |
       |   Non-running tasks (READY, PENDING, FAILED):
       |     Status UNCHANGED during blocker.
       |     READY tasks dispatch normally — if they hit the broken
       |     dependency, they fail and the replanner sees blocker context.

---

## 5. Data Model

### Blocker

    Blocker
        id                  str                 unique identifier
        team_run_id         str                 which run this blocker belongs to
        status              BlockerStatus       ASSESSING | FIXING | RESOLVED | FAILED
        reason              str                 human-readable description of the problem
        root_cause_paths    list of str         the broken files (fix target)
        initiating_task_id  str                 the failed task that triggered the blocker
        fix_task_id         str or None         the task assigned to fix the root cause
        declared_by         str or None         the replanner task that declared this
        fix_summary         str or None         filled when fix completes
        pending_assessments  int                 pause assessments still awaiting response
        created_at          float               timestamp

    Assessment scope is determined structurally: all siblings of the
    initiating task plus their entire subtrees.  blast_radius has been
    removed — there is no file-path guard on pop_ready.
        resolved_at         float or None       timestamp

### BlockerStatus

    ASSESSING       spawning pause assessments for RUNNING agents, waiting for verdicts
    FIXING          all assessments resolved, resolver task is running
    RESOLVED        fix complete, assessed agents resumed, replanner spawned for initiator
    FAILED          resolver could not fix, team run marked FAILED

### TaskRecord Additions

    blocker_id          str or None         which blocker paused this task
    pause_checkpoint    blob or None        pause assessment's display_messages for resume
    pause_verdict       str or None         pause assessment's YES reason

    Only tasks paused from RUNNING have these fields populated.
    Non-running tasks (READY, PENDING, FAILED) are never touched by the
    blocker protocol — their status remains unchanged.

### Resume Behavior

    Only one resume path exists: PAUSED → READY (from formerly-RUNNING tasks).

    Resume: new agent run starting from pause_checkpoint
    Context: pause assessment's full conversation + resume message appended
    The resumed agent sees everything the original did, plus why it was paused,
    plus what was fixed.

    Non-running tasks are not resumed — they were never paused.
    READY tasks dispatch normally throughout the blocker lifecycle.
    PENDING tasks continue waiting for deps.
    The initiating FAILED task is handled by a post-fix replanner spawn
    (see Conductor.on_fix_complete).

---

## 6. Replanner Decision Tree

### Context Available to the Replanner

The replanner is spawned after a developer calls request_replan. It has access to:

    - The failed task's error message and scope
    - All sibling tasks and their statuses (via sibling_stats)
    - Notes from completed and failed siblings (via TaskCenter)
    - Plan health signals (failure rate, retry counts)
    - Children task statuses for expanded siblings
    - The original plan structure

### Three Actions

    Replanner spawns after a task fails
          |
          v
    Read context: failure reason, sibling statuses,
    notes, plan health, children
          |
          v
    "What went wrong?"
          |
          +--- "Missing work. Plan had a gap."
          |     Some tasks are fine, we just need more.
          |     Or a task just needs another attempt with adjustments.
          |
          |     ---> add_tasks
          |          Add new tasks alongside existing siblings.
          |          Can include retried versions of failed tasks
          |          with adjusted descriptions, deps, or scope.
          |          Siblings continue running. No interruption.
          |
          +--- "Shared dependency broken. Not a plan error."
          |     A completed sibling broke something others depend on.
          |     Multiple siblings will hit the same error.
          |
          |     ---> declare_blocker
          |          Pause all siblings and their children.
          |          Conductor spawns a fix task.
          |          Everything resumes after the fix.
          |
          +--- "Plan was fundamentally wrong."
                Wrong decomposition, wrong ordering, wrong approach.
                Need to start over.

                ---> cancel_and_redraft
                     Cancel all siblings and their children.
                     Submit a completely new plan.

### add_tasks Absorbs request_retry

The old request_retry tool is removed. Retry logic is absorbed into the replanner's add_tasks action. When a task just needs another attempt, the replanner creates a new task with the same goal plus failure context. This is strictly more powerful than a blind retry because the replanner can:

    - Adjust the task description based on the failure
    - Add dependencies the original was missing
    - Change the assigned agent
    - Modify the scope
    - Include diagnostic context from the failure

Each attempt is a new task with a clean record. The original stays FAILED with its history preserved.

### Replanner Prompt Guidance

    "You are a replanner. A task has failed. Read the failure context,
     sibling statuses, and plan health, then call exactly ONE action:

     add_tasks — the plan is fine, just needs more work or a retry.
     declare_blocker — a shared dependency is broken, pause siblings.
     cancel_and_redraft — the plan was wrong, cancel and start over."

---

## 7. Blocker Lifecycle

### State Machine

                        replanner calls
                        declare_blocker()
                               |
                               v
                        +------------+
                        | ASSESSING  |
                        |            |
                        | spawn pause assessments for RUNNING agents
                        | non-running tasks UNTOUCHED (stay as-is)
                        +-----+------+
                              |
                              | all assessments resolved (YES/NO/timeout)
                              | all terminated agents saved
                              v
                        +-----------+
                        |  FIXING   |
                        |           |
                        | resolver task dispatched at depth=0
                        | resolver (dedicated role) repairs root cause
                        +-----+-----+
                             / \
                       DONE /   \ FAILED (after 1 retry)
                           /     \
                    +----------+  +-----------+
                    | RESOLVED |  |  FAILED   |
                    |          |  |           |
                    | resume   |  | cancel    |
                    | assessed |  | all PAUSED|
                    | agents   |  | tasks     |
                    | lift     |  | lift      |
                    | guard    |  | guard     |
                    | spawn    |  | log       |
                    | replanner|  | critical  |
                    | for      |  |           |
                    | initiator|  |           |
                    +----------+  +-----------+

### Pause Phase

    Blocker created by replanner
          |
          v
    +-----------------------------------------------------------+
    |                 ASSESSMENT PHASE                           |
    |                                                           |
    |  Scope: all siblings of the initiating task plus their    |
    |  entire subtrees (structural, not file-path based).       |
    |                                                           |
    |  Non-running tasks (READY / PENDING / FAILED):            |
    |    UNTOUCHED. Status remains as-is.                       |
    |                                                           |
    |  Running tasks (ALL siblings+descendants):                |
    |    Spawn pause assessment per agent (parallel).        |
    |    Let the external trigger decide YES or NO.                |
    |    YES: terminate original, save conversation, PAUSED.    |
    |    NO: discard, original continues unaware.               |
    |    TIMEOUT / error: skip (agent keeps running).           |
    |                                                           |
    |  No tiers. No auto-halt. No scope classification.         |
    |  One rule: running = external trigger decides.               |
    |  Non-running = never touched.                             |
    |                                                           |
    +-----------------------------------------------------------+
          |
          | all assessments resolved
          v
    FIXING phase begins

### No Dispatch Guard

There is no dispatch guard. Tasks dispatch freely during active
blockers. Assessment scope is structural (siblings + descendants),
not file-path based.

Tasks that hit the broken dependency fail naturally and trigger
their own `request_replan`. The replanner reads sibling notes
(including auto-notes from active mode) and sees the existing
blocker context — allowing informed recovery decisions without
a dispatch-level throttle. This maximizes parallelism: unaffected
tasks continue working while the resolver fixes the root cause.

### Dedup and Merge

When a second blocker declaration targets the same root cause (overlapping paths), the Conductor merges into the existing blocker rather than creating a new one:

    declare_blocker(paths, reason)
          |
          v
    Search active blockers:
      scope overlap match?
          |
     +----+----+
     |         |
    match    no match
     |         |
     v         v
    MERGE    CREATE
    expand   new blocker
    paths    assess_running
    (union)  spawn_resolver
    re-run
    pause
    for new
    scope

On merge, assess_running re-runs against siblings+descendants. Tasks already PAUSED (blocker_id set) are skipped. Only newly-RUNNING agents that were not yet assessed get assessed. Non-running tasks remain untouched.

---

## 8. External Trigger Module

An external trigger spawns an ephemeral agent that inherits a conversation snapshot, has constrained tools, and is guaranteed to produce a valid tool call. It replaces the former external trigger module with a unified tool-call-based design shared with the post-run submission phase.

The external_trigger module is used by both the Conductor (for pause assessment) and the TaskCenter active mode (for progress reporting). Both paths use the same `runner.run()` loop.

### Core Principle

    The original agent NEVER sees the external trigger agent.
    The external trigger agent sees everything the original saw PLUS one question.
    The original is never interrupted (unless pause assessment says YES).

### External Trigger Runner

    runner.run()
        Shared LLM loop for both external_trigger and post_run phases.

        Parameters
            messages            list of dict    frozen conversation snapshot
            system_prompt       str             system prompt for the session
            prompt              str             injected as final user message
            tools               list of BaseTool constrained tool set
            api_client          Any             Anthropic-compatible client
            max_tokens_per_turn int             output cap (default 500)
            model               str or None     optional model override

        Execution
            Always uses tool_choice="any" — every turn produces a tool call.
            Validates with Pydantic (tool.input_model).
            Retries indefinitely on validation failure (error fed back).
            Only exit: successful validated tool call or asyncio cancellation.

    run_external_trigger()
        Agent identity wrapper over runner.run().
        Adds agent_name + logging for observability.
        Used by Conductor and TaskCenter (not by Executor post-run).

    RunResult
        tool_name           str             which tool the LLM chose
        tool_input          dict            raw input
        validated           BaseModel       Pydantic-validated input
        conversation        list of dict    full trail for audit/resume
        turns_used          int             how many turns before success

### Tool Type System

    ToolType = Literal["normal", "post_run", "external_trigger"]

    Each BaseTool has tool_types: set[ToolType] (can have multiple).
    PauseVerdictTool: tool_types = {"external_trigger"}
    PostNoteTool:     tool_types = {"external_trigger", "post_run"}
    Posthook tools:   tool_types = {"post_run"}

### Two Concrete Uses

    Pause Assessment (external_trigger/pause_assessment.py)
        Purpose: decide if a running agent is affected by a blocker
        Triggered by: Conductor
        Tool: PauseVerdictTool (answer: YES/NO, reason: str)
        Prompt: "Based on your work, does your task depend on {broken_files}?"
        Output: RunResult → PauseVerdict (YES/NO + reason)
        Effect: YES → terminate original, save conversation as resume point
                NO → discard, original unaware

    Checkpoint Note (external_trigger/tc_note.py)
        Purpose: produce a progress note on behalf of a running agent
        Triggered by: TaskCenter active mode
        Tool: PostNoteTool (content: str, scope_paths: list)
        Prompt: "Summarize progress. Report blockers." (varies by trigger)
        Output: RunResult → NoteSummary
        Effect: note posted under original task's ID, original unaware

### pause assessment — Blocker Impact Assessment

#### Fork Diagram

    Time --------------------------->

    Agent-A          | tool 1 | tool 2 | tool 3 | tool 4 | tool 5 |
    (original)       | read   | edit   | test   | read   | edit   |
                     |        |        |   ^    |        |   X    |
                     |        |        |   |    |        |   terminated
                     |        |        |   |    |        |   (asyncio.cancel)
                     |        |        | snapshot         |
                     |        |        |   |    |        |
    PauseAssessment  |        |        |   +-->+--------+|
    (1 LLM call)     |        |        |       | sees:  ||
                     |        |        |       | tool1-3||
                     |        |        |       | +      ||
                     |        |        |       |blocker ||
                     |        |        |       |question||
                     |        |        |       |answers:||
                     |        |        |       |"YES"   ||
                     |        |        |       +---+----+|
                     |        |        |           |
                     |     (fix happens here)      |
                     |        |        |           |
    Resumed agent    |        |        |           +-->+----------------+
    (new run)        |        |        |               | sees:         |
                     |        |        |               | tool1-3       |
                     |        |        |               | + blocker Q   |
                     |        |        |               | + "YES" answer|
                     |        |        |               | + resume msg  |
                     |        |        |               | continues...  |
                     |        |        |               +----------------+

The original did tool calls 4 and 5 after the snapshot. Those are lost. The resumed agent starts from the external trigger agent's conversation (snapshot at tool 3 plus blocker question plus YES answer). This is correct — tool calls 4 and 5 happened against broken code and their results are unreliable.

#### Assessment Says NO

    Agent-B          | tool 1 | tool 2 | tool 3 | tool 4 | tool 5 | DONE
    (original)       | read   | edit   | test   | read   | edit   | submit
                     |        |        |   ^    |        |        |
                     |        |        | snapshot         |        |
                     |        |        |   |    |        |        |
    PauseAssessment  |        |        |   +-->+--------+|        |
                     |        |        |       |"NO: my ||        |
                     |        |        |       | task is||        |
                     |        |        |       | bag/   ||        |
                     |        |        |       | only"  ||        |
                     |        |        |       +--------+|        |
                     |        |        |         |       |        |
                     |        |        |      discard    |        |
                     |        |        |                  |        |
                     Agent-B finished normally.                    |
                     Never saw the external trigger agent. Zero impact.    |

#### pause assessment Input

The external trigger agent's input is the original agent's full display_messages plus one new user message:

    Input:
        system_prompt: "You are a blocker assessment assistant."
        messages:
            [all display_messages from original agent — every tool call,
             every tool result, everything the agent has seen and done]
            +
            one new user message:
                "BLOCKER CHECK
                 A shared dependency has been reported broken.
                 Broken files: dask/compatibility.py
                 Problem: __getattr__ replaced _EMSCRIPTEN, broke parse import

                 Based on your work so far in this conversation,
                 does your task depend on any of these files?
                 Call the pause_verdict tool with your assessment."

        max_tokens_per_turn: 200
        tools: [PauseVerdictTool]
        tool_choice: "any" (guaranteed tool call via runner.run())

    Output: RunResult → PauseVerdictInput(answer="YES"|"NO", reason="...")

    Example:
        PauseVerdictInput(
            answer="YES",
            reason="I imported dask.compatibility in tool call 1 to access
                    the parse function. My HDF reader depends on it."
        )

The external trigger agent has full context — it saw every tool call the original agent made, every file read, every import. It knows exactly whether it touched the broken dependency.

#### PauseVerdict

    PauseVerdict (dataclass in pause_assessment.py)
        task_id         str                 which task was assessed
        answer          str                 "YES" or "NO" (from PauseVerdictInput)
        reason          str                 the reasoning
        conversation    list of dict        the full conversation trail from runner
                                            saved as resume checkpoint if YES
        turns_used      int                 how many runner turns before success

    PauseVerdictInput (Pydantic model in tools/external_trigger/)
        answer          Literal["YES", "NO"]    normalized to uppercase
        reason          str                     reasoning

#### Timeout

    External trigger agent spawned
          |
          +--- LLM responds within 30 seconds ---> normal YES/NO path
          |
          +--- LLM takes more than 30 seconds ---> timeout
                    |
                    v
               Skip (agent keeps running)
               A slow LLM call should not pause an unaffected agent.
               If the agent is actually affected, it will fail later
               and the replanner will see active blocker context.

#### Termination — External, No query.py Changes

The executor manages the agent run as an asyncio task. The Conductor terminates by cancelling the asyncio task. This is standard asyncio cancellation. The query loop catches CancelledError and cleans up. No modification to query.py required.

#### Safety Net — Replanner-Based

If a pause assessment says NO (or was skipped due to timeout)
but the original agent later fails, the normal replanner flow handles
it.  The replanner receives active blocker context (see G-8) and can
decide whether to merge the failure into the existing blocker via
declare_blocker, or treat it as an independent failure via add_tasks.
No Conductor.on_task_failed method is needed.

### CheckpointTask — Progress Reporter

#### Fork Diagram

    Agent-A          | tool 1 | tool 2 | tool 3 | tool 4 | tool 5 | tool 6 |
    (original)       | read   | edit   | edit   | edit   | edit   | edit   |
                     |        |        |        |        |   ^    |        |
                     |        |        |        |        | snapshot        |
                     |        |        |        |        |   |    |        |
    CheckpointTask   |        |        |        |        |   +-->+------+ |
    (1 LLM call)     |        |        |        |        |       |summa-| |
                     |        |        |        |        |       |rize  | |
                     |        |        |        |        |       |prog- | |
                     |        |        |        |        |       |ress  | |
                     |        |        |        |        |       +--+---+ |
                     |        |        |        |        |          |     |
                     |        |        |        |        |     note posted|
                     |        |        |        |        |     to TC      |
                     |        |        |        |        |                |
                     Agent-A continues working. Never saw the checkpoint.|
                     Other agents see the note in TaskCenter.            |

#### Two Trigger Variants

    EDIT_CHECKPOINT (triggered after 5 edits without post_note)
        Prompt:
            "Based on this agent's work so far, write a progress note.
             Focus on: what files were edited and why.
             Include file paths and specific changes.
             Keep under 300 words."

    TURN_CHECKPOINT (triggered after 10 turns without any posthook call)
        Prompt:
            "Based on this agent's work so far, write a progress note.
             Include:
             1. What the agent has accomplished
             2. Current status (working / stuck / nearly done)
             3. Whether the agent appears blocked by code another
                task broke (include file path and error if so)
             Keep under 300 words."

        The turn checkpoint explicitly asks about blockers. This feeds
        the replanner's decision via sibling notes from
        `read_sibling_notes(...)`.

#### Note Attribution

    The checkpoint note is posted with:
        task_id         original task's ID
        agent_name      original agent's name + " (auto)"
        scope_paths     original task's scope_paths
        timestamp       current time

    To siblings and the replanner, it looks like the original agent
    posted a note. The "(auto)" suffix distinguishes it from
    agent-authored notes for auditing purposes.

### Module Structure

    external_trigger/ (NEW)

    Contains:
        external trigger           base dataclass + run() method
        RunResult         result dataclass
        pause assessment     blocker assessment (used by Conductor)
        PauseVerdict            parsed YES/NO result
        CheckpointTask          progress reporting (used by TaskCenter active mode)

    Consumers:
        Conductor               imports pause assessment, PauseVerdict
        TaskCenter active mode     imports CheckpointTask
        Executor                provides display_messages snapshots to both

    The module is standalone — no dependency on query.py, Conductor,
    or TaskCenter active mode. Those are consumers, not dependencies.
    The external trigger agent only needs an API client to make the LLM call.

### external trigger Uses — Complete Map

    Trigger              external trigger Type       Output             Effect

    Blocker declared     pause assessment      YES/NO verdict     terminate if YES
    (Conductor)                                                      none if NO

    5 edits              CheckpointTask           progress note      none on original
    (TaskCenter active mode)                         posted to TC

    10 turns             CheckpointTask           progress note      none on original
    (TaskCenter active mode)                         + blocker check
                                                  posted to TC

---

## 9. Conductor

### What the Conductor Is

The Conductor is a deterministic, non-LLM system actor within the TeamRun. It executes blocker mechanics: pause, assess, terminate, fix, resume. It makes no judgment calls. All judgment comes from the replanner (which declares the blocker) and pause assessments (which answer YES/NO).

### What the Conductor Is Not

The Conductor is not an LLM agent. It never calls a model. It never reasons about code, imports, or blast radius. This is critical for speed (sub-second blocker response), reliability (deterministic behavior), cost (no LLM calls per failure), and authority (system-level powers guarded by deterministic predicates).

The Conductor spawns resolver tasks (a dedicated role, not a normal developer) for fixing root causes, and spawns a post-fix replanner for the blocker-initiating task.

### Class Definition

    Conductor
        Constructor
            team_run            reference to the owning TeamRun
            blocker_store       BlockerStore or None (optional durable persistence)
            _active_blockers    dict mapping blocker_id to Blocker (in-memory)
            _executor_snapshots dict mapping task_id to display_messages (in-memory)

        Snapshot Registry
            register_snapshot(task_id, snapshot)
                Track display_messages for running tasks.
                Called by executor after each tool result.

        Recovery
            restore()
                Reload active blockers from the store on crash/restart.

        Blocker Lifecycle
            create_blocker(replanner_verdict)
                Called when replanner invokes declare_blocker.
                Creates Blocker record from replanner's assessment
                (including initiating_task_id from the failed task).
                Calls assess_running.

            assess_running(blocker)
                Queries all siblings of the initiating task plus their
                subtree children.  Filters to RUNNING status only.
                Spawns one pause assessment per RUNNING task via
                asyncio.gather. Each assessment is a single LLM call.
                TIMEOUT / error → skip (agent keeps running).
                Non-running tasks (READY/PENDING/FAILED) are NEVER touched.

            _run_pause_assessment(executor, blocker) returns PauseVerdict
                Snapshots display_messages from executor.
                Single LLM call: no tools, max_tokens 200, timeout 30 seconds.
                Parses YES/NO/TIMEOUT from response.

            _on_pause_yes(executor, blocker, assessment_conversation, reason)
                Saves assessment_conversation as pause_checkpoint on the task.
                Cancels executor's asyncio task (external termination).
                Marks task PAUSED.
                Decrements pending_assessments.

            _on_pause_no(executor, blocker, reason)
                Logs dismissal and reason.
                Discards assessment result.
                Original agent continues unaware.
                Decrements pending_assessments.

            spawn_resolver(blocker)
                Creates a resolver task using the dedicated `resolver` role
                when available, else falls back to a developer-role agent.
                The task is scoped to root_cause_paths and is inserted through
                TaskCenter so budget accounting and task events stay consistent.
                Resolver instructions explicitly say:
                    success  -> `post_note(...)`
                    failure  -> `request_replan(...)`

            on_fix_complete(blocker, fix_summary)
                Called when the resolver task completes successfully
                (typically after `post_note(...)`).
                Stores fix_summary on blocker.
                Calls resume_assessed(blocker, fix_summary).
                Removes blocker from active set.
                Requests a post-fix replanner for blocker.initiating_task_id.
                Blocker status set to RESOLVED.

            on_fix_failed(blocker)
                Called when the resolver task calls `request_replan(...)`
                or its runner fails outright.
                Cancels all PAUSED tasks for this blocker.
                Marks the team run FAILED.
                Blocker status set to FAILED.

            resume_assessed(blocker, fix_summary)
                Transitions all PAUSED tasks with this blocker_id to READY.
                For each: new agent run from pause_checkpoint with resume
                message appended. Only formerly-RUNNING tasks are in this set.
                Non-running tasks were never paused — they dispatch
                normally throughout the blocker lifecycle.

        Queries
            has_active_blocker() returns bool
                Whether any blocker is currently active.
            blocker_for_fix_task(task_id) returns Blocker or None
                Look up whether a task is a resolver fix task.

---

## 10. Toolkit Changes

### Developer / Reviewer Posthook — Before and After

    BEFORE (3 tools):
        post_note          "I'm done, here's what I did"
        request_retry      "I failed, same task again"
        request_replan     "I failed, need a different approach"

    AFTER (2 tools):
        post_note          "I'm done, here's what I did"       (unchanged)
        request_replan     "I failed"                           (unchanged interface)

    REMOVED:
        request_retry      absorbed into replanner's add_tasks

The developer no longer distinguishes between "retry" and "replan." It just reports failure. The replanner decides what to do.

### Planner Posthook — Unchanged

    submit_plan            "Here's the task decomposition"

### Replanner Posthook — Before and After

    BEFORE (1 tool, overloaded):
        submit_replan      add_tasks + cancel_ids bundled together
                           called after siblings already auto-cancelled

    AFTER (3 tools, clear intent):
        add_tasks           add new tasks alongside existing siblings
                            can include retried versions of failed tasks
                            siblings continue running, no interruption

        declare_blocker     pause siblings + their children
                            triggers Conductor to spawn fix task
                            everything resumes after fix

        cancel_and_redraft  cancel all siblings + their children
                            submit a completely new plan

    REMOVED:
        submit_replan       replaced by the three tools above

### declare_blocker Tool Definition

    declare_blocker
        Parameters:
            root_cause_paths    list of str     the broken files (fix target)
            reason              str             why this is systemic
            suggestion          str or None     how to fix (optional)

        Note: blast_radius has been removed. Assessment scope is
        structural (siblings + descendants of the initiating task),
        not file-path based.

        Available to: replanner role only

        Returns: confirmation that blocker was created

        Side effect: Conductor creates Blocker and begins pause protocol

### add_tasks Tool Definition

    add_tasks
        Parameters:
            tasks               list of TaskSpec     new tasks to insert

        Available to: replanner role only

        Returns: confirmation with count of inserted tasks

        Side effect: TaskCenter inserts tasks as siblings of the failed task.
        Existing siblings are untouched.

### cancel_and_redraft Tool Definition

    cancel_and_redraft
        Parameters:
            tasks               list of TaskSpec     the new plan

        Available to: replanner role only

        Returns: confirmation with cancel count and insert count

        Side effect: TaskCenter cancels specified siblings (by cancel_ids)
        and their dependents, then inserts the new tasks.

### Resolver Posthook — Dedicated Role, Shared Terminal Tools

    RESOLVER TASK:
        Dedicated role: `resolver`
        Terminal tools: `post_note`, `request_replan`
        Executor identifies fix tasks via conductor.blocker_for_fix_task().

    Semantics:
        `post_note`       resolver succeeded -> Conductor.on_fix_complete
        `request_replan`  resolver failed    -> Conductor.on_fix_failed

    The resolver is a dedicated role because the dispatch guard and the
    Conductor treat it specially, but it intentionally reuses the same
    terminal submission tools as developers. No separate `submit_fix` or
    `abandon_fix` tools exist in the current implementation.

### Infrastructure Retry — Preserved at Executor Level

Agent-level request_retry is removed, but infrastructure failures (OOM, timeout, network errors) still auto-retry at the executor level. These are transient failures that do not need replanning:

    Executor catches exception
          |
          +--- Infrastructure failure? (worker_exception, runner_exception)
          |       |
          |       YES --> auto-retry at executor level
          |               uses existing retry_count / max_retries
          |               no replanner spawned
          |
          +--- Agent failure? (agent called request_replan or submitted error)
                  |
                  --> spawn replanner
                      replanner decides: add_tasks / blocker / nuke

---

## 11. TaskCenter Changes (formerly Dispatcher)

### request_replan — No Auto-Cancel

The former request_replan in dispatcher_store.py auto-cancelled all pending/ready/expanded siblings and cascade-cancelled their dependents BEFORE the replanner ran. This was too aggressive — it destroyed work before anyone assessed whether that work should be destroyed.

    CURRENT request_replan flow:
        1. Mark failing task FAILED
        2. Cancel pending/ready/expanded siblings        <-- destructive
        3. Cascade cancel dependents of cancelled        <-- destructive
        4. Collect done siblings as deps for replanner
        5. Insert replanner task

    PROPOSED request_replan flow:
        1. Mark failing task FAILED
        2. Insert replanner task (siblings UNTOUCHED)    <-- replanner decides

The replanner now sees live siblings. It can assess their state, read their notes, and decide: add alongside them, pause them, or cancel them.

### TaskStore SQL Methods (delegated from TaskCenter)

    pause_running_task(task_id, blocker_id, pause_checkpoint, pause_verdict)
        UPDATE tasks SET status = 'paused', blocker_id = blocker_id,
            pause_checkpoint = checkpoint, pause_verdict = verdict
        WHERE id = task_id AND status = 'running'
        Used only for RUNNING tasks after pause assessment says YES.
        Non-running tasks are never paused.

    resume_paused_tasks(run_id, blocker_id) returns int
        UPDATE tasks SET status = 'ready', blocker_id = NULL
        WHERE team_run_id = run_id AND blocker_id = blocker_id
            AND status = 'paused'
        Returns count of resumed tasks. All resumed tasks were
        formerly RUNNING — they re-enter as READY with pause_checkpoint
        for conversation restoration.

    cancel_paused_tasks(run_id, blocker_id) returns int
        UPDATE tasks SET status = 'cancelled', blocker_id = NULL
        WHERE team_run_id = run_id AND blocker_id = blocker_id
            AND status = 'paused'
        Used when resolver fails and team run is marked FAILED.

### pop_ready — No Guard

    pop_ready is a simple atomic claim with no guard logic.
    SQL selects the next READY candidate with pending_dep_count = 0
    and atomically sets it to RUNNING.

    During an active blocker, READY tasks continue to dispatch normally.
    Tasks that hit the broken dependency fail and trigger request_replan.
    The replanner reads sibling notes (including auto-notes) and sees
    the existing blocker context for informed recovery decisions.

---

## 12. Resume Protocol

### Resume Message

When a formerly-paused task dispatches, the executor injects a resume message as Priority 0 context (never trimmed). This is handled in task_center.context_for, not in query.py.

    Resume message structure:

        RESUME — Blocker Resolved

        Your task was paused because a shared dependency was broken.

        Blocker: (reason from Blocker record)
        Broken files: (root_cause_paths)
        Reported by: (replanner that declared the blocker)
        Fix applied: (fix_summary from resolver)

        Why you were paused (your own assessment):
        (pause_verdict — the pause assessment's YES reason)

        Your progress before pause:
        (pause_checkpoint summary)

        Continue from where you left off.
        Re-read any files from the affected scope that you previously read.

### Resume — From pause assessment Checkpoint

Only formerly-RUNNING tasks are paused and resumed. They resume from the pause assessment's conversation, not from scratch:

    1. Load pause_checkpoint (the pause assessment's display_messages)
    2. Append the resume message as a new user message
    3. Start a new agent run with these messages as the conversation history
    4. The query loop runs normally from there

The resumed agent sees:
    - Everything the original did before the snapshot (all tool calls)
    - The blocker question (injected into the pause assessment)
    - Its own YES answer (the assessment's reasoning about why it was affected)
    - The resume message (what was fixed, continue working)

The agent naturally continues from the snapshot point.

### Non-Running Tasks — Not Paused, Not Resumed

Non-running tasks (READY, PENDING, FAILED) are never paused by the blocker protocol. Their status remains unchanged throughout the blocker lifecycle.

    READY tasks: dispatch normally throughout the blocker lifecycle. If
    they hit the broken dependency, they fail and trigger request_replan.
    The replanner sees the existing blocker context via sibling notes
    and can make informed recovery decisions. No resume message needed.

    PENDING tasks: continue waiting for dependencies. Unaffected.

    The initiating FAILED task: handled by a post-fix replanner spawn.
    The Conductor spawns a replanner scoped to the initiating task after
    the resolver completes. The replanner reads the fix_summary and the
    original failure, then calls add_tasks with a retry or adjusted task.

---

## 13. Task Center Integration

The Task Center is the shared context backbone of the coordination system. The following additions strengthen it for the replanner and improve note discipline across all agents.

### 13.1 read_sibling_notes — Structural Sibling Context

The current implementation keeps `read_notes(...)` generic and provides a
separate TaskCenter helper:

    read_sibling_notes(parent_id, *, keyword=None, scope_paths=None)

`read_sibling_notes(...)` resolves the sibling subtree internally via
`_sibling_subtree_ids`, then delegates to the existing note store with the
same keyword and scope-path filtering rules. This keeps the general-purpose
`read_notes(...)` API stable while giving replanners a single sibling-aware
entry point.

#### What the Replanner Sees

    read_sibling_notes(parent_id=...) returns:

    --- task hdf-01 (developer) [scope: dask/dataframe/io/hdf.py] ---
    "Hit ImportError on dask.compatibility.parse — file was changed
     by fix-compat task. This is a shared dependency issue."

    --- task hdf-02 (developer) [scope: dask/dataframe/io/hdf.py] ---
    "ImportError: cannot import name 'parse' from dask.compatibility.
     Same error as hdf-01. Root cause is in compatibility.py, not my scope."

    --- task fix-compat (developer) [scope: dask/compatibility.py] ---
    "Replaced _EMSCRIPTEN with __getattr__ mechanism. Removed direct
     parse import in favor of lazy attribute lookup."

The replanner reads this and immediately sees: fix-compat broke a shared
dependency, hdf-01 and hdf-02 both report the same ImportError. This is the
evidence it needs to call declare_blocker versus add_tasks.

### 13.2 TaskCenter Active Mode

See separate document: [task-center-active-mode.md](task-center-active-mode.md)

The TaskCenter gains an active mode where it tracks agent activity (edits,
turns, posthook calls) and spawns external trigger agents to auto-generate
progress notes when agents are silent too long. This complements the blocker
protocol by ensuring the replanner has rich sibling context via
`read_sibling_notes(...)`.

Key relationship to the blocker protocol: the turn-trigger external trigger prompt explicitly asks about blockers. Auto-generated notes surface blocker evidence early, giving the replanner higher-confidence signals for declare_blocker decisions.

### 13.3 Conversation Snapshot Mechanism

The external trigger module (both pause assessment and auto-note generation) needs a read-only snapshot of the running agent's conversation. The current Executor delegates to a QueryRunner callable and does not retain conversation state. This requires a lightweight extension.

#### Design

The query loop (run_query_loop) maintains display_messages internally. To expose a snapshot without modifying query.py internals, the loop accepts an optional callback:

    run_query_loop signature gains one optional parameter:

        on_turn: Callable[[list[ConversationMessage]], None] or None

    At the top of each turn (alongside ScopeChangeBuffer flush),
    the loop calls on_turn(display_messages) if provided.

    The executor provides this callback:

        def _on_turn(self, messages):
            self._latest_messages = messages

    When the Conductor or TaskCenter needs a snapshot:

        snapshot = list(executor._latest_messages)

    This is a shallow copy of the append-only list. Safe because
    display_messages is never mutated in place — only appended to.

#### Why This Is Minimal

    query.py changes: one optional parameter + one callback invocation
    No message injection. No display_messages mutation.
    The callback is fire-and-forget — no return value, no blocking.
    If on_turn is None (non-team mode), the line is skipped entirely.

#### Diagram

    query loop (inside query.py):
          |
          | top of each turn
          v
    if on_turn is not None:
        on_turn(display_messages)     # one line, fire-and-forget
          |
          v
    (rest of the turn proceeds normally)


    executor (outside query.py):
          |
          | _on_turn callback stores reference
          v
    self._latest_messages = messages
          |
          v
    Conductor or TaskCenter reads snapshot when needed:
        snapshot = list(self._latest_messages)

### 13.4 No Dispatch Guard

There is no dispatch guard on `pop_ready`. Tasks dispatch freely during
active blockers. This maximizes parallelism: unaffected tasks continue
working while the resolver fixes the root cause.

Tasks that hit the broken dependency fail naturally and trigger
`request_replan`. The replanner reads sibling notes (including auto-notes
from active mode) and sees the existing blocker context, allowing
informed recovery decisions. This is simpler and more concurrent than
a global dispatch throttle.

### 13.5 Blocker Persistence

The active-blocker set is cached in-memory on the Conductor for fast
lookups, and blocker records are also persisted through `BlockerStore`.

    In-memory:
        Fast access for fix-task lookup and active blocker queries.

    Durable:
        BlockerStore saves blocker status, root_cause_paths, initiating task,
        fix_task_id, and resolution/failure metadata.

    Recovery:
        On TeamRun restart, Conductor.restore() reloads active blockers from
        BlockerStore and task replay restores paused-task metadata from the
        event log and task rows.

### 13.6 Concurrent Blockers on the Same Task

A task has a single `blocker_id` field. If a second blocker encounters an
already-paused task, it skips that task. Resume is intentionally simple:
the original blocker clears `blocker_id` and returns the task to READY.
If another blocker is still active and the task hits the same broken
dependency again after resuming, it fails and the replanner sees the
second blocker context via sibling notes.

    resume_paused_tasks(blocker_id):
        for each task with this blocker_id and status=PAUSED:
            transition to READY
            clear blocker_id

This avoids the complexity of a `blocker_ids` list while preserving safety
through the note-based replanner awareness.

### 13.7 Database Migration

The following schema changes require a migration:

    ALTER TABLE tasks ADD COLUMN blocker_id TEXT;
    ALTER TABLE tasks ADD COLUMN pause_checkpoint BYTEA;
    ALTER TABLE tasks ADD COLUMN pause_verdict TEXT;

    No paused_from column — only RUNNING tasks can be paused.
    Blockers themselves are persisted through BlockerStore and restored
    on restart. Migration is part of the blocker-protocol rollout.

---

## 14. Scope and Boundaries

### Blocker Scope — Siblings and Their Children Only

A blocker declared by a replanner affects only the siblings of the failed task and their children. It does not affect tasks in other subtrees.

    Parent (EXPANDED)
    +-- task-A (DONE — broke shared dep)
    +-- task-B (FAILED — hit broken dep, triggered replan)
    +-- task-C (READY)                    <-- sibling, in scope
    +-- task-D (RUNNING)                  <-- sibling, in scope
    |   +-- task-D1 (READY)              <-- child of sibling, in scope
    |   +-- task-D2 (RUNNING)            <-- child of sibling, in scope
    +-- task-E (EXPANDED)                 <-- sibling, in scope
    |   +-- task-E1 (DONE)
    |   +-- task-E2 (PENDING)            <-- child of sibling, in scope
    |
    +-- [replanner task inserted here, same parent]

    Everything outside this parent is NOT in scope.

### Cross-Subtree Blockers

If the same broken dependency affects tasks in a different subtree (different parent), those tasks fail independently and trigger their own request_replan. Their replanner makes its own assessment and may also declare_blocker. This results in two independent blockers, each scoped to their own subtree.

This is slightly redundant (two fix tasks for the same file) but correct and dramatically simpler than cross-subtree coordination. The second fix task sees the file already repaired and completes immediately.

### Root Cause Paths vs Structural Scope

    root_cause_paths    The specific broken files. Used by the resolver task
                        and included in pause-assessment prompts.

    structural scope    The initiating task's siblings plus their descendants.
                        Used to decide which RUNNING tasks are assessed.

There is no separate `blast_radius` field in the implemented protocol.
Shared impact is inferred structurally from the task tree, not from a
replanner-supplied path superset.

---

## 15. Budget and Safety

### Safety Properties

    request_replan
        Fails only the calling task and spawns a replanner.
        It does not auto-cancel siblings.

    declare_blocker
        Touches only RUNNING siblings + descendants during assessment.
        READY/PENDING/FAILED tasks keep their state.

    no dispatch guard
        READY tasks dispatch freely during blockers. Tasks that hit
        the broken dependency fail naturally and trigger request_replan,
        where the replanner sees blocker context via sibling notes.

    resolver failure
        Cancels PAUSED tasks for that blocker and fails the run.

    event + task persistence
        Blocker metadata, pause checkpoints, and pause verdicts are emitted
        and replayed so resume/failure cycles survive restart.

### Resolver Outcome Policy

    Resolver task completes
          |
          +--- post_note ---------> blocker RESOLVED
          |                        resume assessed agents with fix context
          |                        remove blocker from active set
          |                        spawn replanner for initiating task
          |
          +--- request_replan ---> blocker FAILED
                                   cancel all PAUSED tasks
                                   mark team run as FAILED

---

## 16. Walkthrough — The compatibility.py Scenario

### Initial State

    Root Planner (EXPANDED)
    +-- plan-A (EXPANDED)
        +-- fix-compat       scope=[dask/compatibility.py]     DONE
        +-- hdf-01           scope=[dask/dataframe/io/hdf.py]  RUNNING
        +-- hdf-02           scope=[dask/dataframe/io/hdf.py]  FAILED
        +-- hdf-03           scope=[dask/dataframe/io/hdf.py]  READY
        +-- hdf-04 .. hdf-32 scope=[dask/dataframe/io/...]     READY

fix-compat completed and broke dask/compatibility.py. hdf-02 failed with ImportError. hdf-01 is still running. hdf-03 through hdf-32 are waiting.

### Step 1 — Developer Reports Failure

hdf-02 calls request_replan("ImportError: cannot import parse from dask.compatibility").

Dispatcher marks hdf-02 FAILED. Inserts replanner task under plan-A. Siblings are NOT cancelled (new behavior).

### Step 2 — Replanner Assesses

Replanner spawns and reads context:
- hdf-02 failed: ImportError on dask.compatibility
- fix-compat DONE: recently modified dask/compatibility.py
- hdf-01 RUNNING: same scope area
- hdf-03 to hdf-32 READY: all in dask/dataframe scope
- Plan health: 1 failure out of 2 started

Replanner judges: fix-compat broke a shared dependency. This is systemic. All dask tasks will hit the same error.

Replanner calls declare_blocker with root_cause_paths=["dask/compatibility.py"],
reason="fix-compat introduced broken __getattr__ that removes parse import",
suggestion="revert __getattr__, restore direct imports".

### Step 3 — Conductor Assesses

Conductor creates Blocker with initiating_task_id=hdf-02. Begins assess_running:

    Non-running tasks (UNTOUCHED):
        hdf-03 to hdf-32: stay READY. Pop_ready guard blocks dispatch.
        hdf-02: stays FAILED. Will be handled by post-fix replanner.

    pause assessment (running agents):
        hdf-01 is RUNNING with scope dask/dataframe/io/hdf.py.
        Conductor spawns pause assessment for hdf-01.

        Assessment sees hdf-01's full conversation.
        Assessment sees: "BLOCKER CHECK: dask/compatibility.py is broken."
        Assessment answers: "YES: I imported dask.compatibility in my
        first tool call to read the parse function."

        Conductor terminates hdf-01's executor.
        Saves assessment conversation as pause_checkpoint.
        hdf-01: RUNNING to PAUSED.

### Step 4 — State After Assessment

    Root Planner (EXPANDED)
    +-- plan-A (EXPANDED)
        +-- fix-compat       DONE
        +-- hdf-01           PAUSED (has pause_checkpoint)
        +-- hdf-02           FAILED (untouched, initiating task)
        +-- hdf-03 .. hdf-32 READY  (dispatch normally, fail if they hit broken dep)
        +-- replanner        DONE
    
    resolver-node (READY, depth=0, parent=None)   <-- spawned by Conductor

plan-A stays EXPANDED because hdf-01 is PAUSED (non-terminal).

### Step 5 — Fix

resolver-node dispatches. A resolver agent scoped to dask/compatibility.py
reads the file, reverts the __getattr__ mechanism, restores the direct parse
import, and reports success with post_note. DONE.

Conductor receives on_fix_complete. fix_summary = "restored direct parse import in compatibility.py".

### Step 6 — Resume + Post-Fix Replanner

Conductor executes on_fix_complete:

    1. Resume assessed agents:
        hdf-01 (PAUSED):
            New agent run started from pause_checkpoint.
            Assessment conversation included: tool calls 1-3, blocker question,
            "YES: I imported dask.compatibility..." answer.
            Resume message appended: "Fix applied: restored direct parse import.
            Continue from where you left off."

    2. Spawn replanner for initiating task (hdf-02):
        Replanner reads: hdf-02's failure reason, fix_summary, current state.
        Replanner calls add_tasks with a retry task for hdf-02's goal,
        including fix context in the new task description.

### Step 7 — Completion

hdf-01 resumes and completes. hdf-03 to hdf-32 dispatch and complete. The replanner's retry task for hdf-02 dispatches and completes. plan-A's children all reach DONE. plan-A promotes to DONE via maybe_promote_expanded_parent.

### Cost Comparison

    Without blocker protocol:
        32 failures + 32 retries + 32 re-failures + multiple replanners
        Total: 64+ task executions

    With blocker protocol:
        1 failure (hdf-02) + 1 replan + 1 resolver + 1 post-fix replanner
        + 1 resume (hdf-01) + 31 normal dispatches (hdf-03..32) + 1 retry (hdf-02)
        Total: 37 task executions
        Saved: 27+ wasted executions

---

## 17. Implementation Snapshot

This section is the authoritative benchmark-facing summary of the current
implementation. Earlier planning notes with alternative resolver tooling or
sibling-note APIs are superseded by the runtime contract below.

### Runtime Files

    backend/src/team/runtime/conductor.py
        Blocker lifecycle, pause-assessment fan-out, resolver spawning,
        dispatch guard, post-fix replanning, and run-failure handling.

    backend/src/team/runtime/executor.py
        Conversation snapshot registration, post-run submission routing,
        resolver success/failure handoff, pause-aware completion, and
        backward-compatible integration with lightweight queue/run stubs.

    backend/src/team/task_center.py
        Unified task lifecycle, pause/resume/cancel transitions,
        sibling-note helpers, active-mode counters, and event emission.

    backend/src/team/persistence/task_store.py
        SQL persistence for task graph mutations, including paused-task
        blocker fields and resumed task replacement.

    backend/src/team/persistence/events.py
    backend/src/team/runtime/rehydration.py
        Event payloads and replay for blocker_id, pause_checkpoint,
        pause_verdict, pending_dep_count, and task status transitions.

    backend/src/tools/posthook/toolkit.py
        Role-aware terminal tools:
            developer/reviewer/resolver -> post_note, request_replan
            replanner                   -> add_tasks, declare_blocker,
                                           cancel_and_redraft

    backend/src/tools/context/toolkit.py
        Main-loop context toolkit remains read-only:
            read_notes
            context_changed_since
        `post_note` is defined here but exposed only in post-run and
        external-trigger phases.

### Behavioral Invariants

    request_replan(task_id, request)
        Marks only the failing task FAILED and inserts a replanner task.
        Siblings are not auto-cancelled.

    create_blocker(...)
        Assesses RUNNING siblings + descendants only.
        READY/PENDING/FAILED tasks keep their status unchanged.

    no dispatch guard
        READY tasks dispatch freely during blockers. Tasks that hit
        the broken dependency fail and the replanner sees blocker
        context via sibling notes.

    resolver task
        Dedicated role when available.
        Success via post_note      -> on_fix_complete.
        Failure via request_replan -> on_fix_failed.

    on_fix_failed(...)
        Cancels PAUSED tasks for the blocker and fails the team run.

    active mode
        Auto-notes are generated by TaskCenter through external-trigger
        execution, not by interrupting the running agent.

### Verification Focus

    - Replanners read sibling-visible notes before recovery decisions.
    - Auto-notes surface blockers early with file/error/scope specificity.
    - Pause checkpoints and blocker metadata survive event replay.
    - Resolver failure is terminal for the run, not a silent unblock.
