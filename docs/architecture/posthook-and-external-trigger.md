# Posthook and External Trigger

## ToolType Classification

The `ToolType` literal defines three execution phases where tools participate:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          ToolType State Diagram                             │
└─────────────────────────────────────────────────────────────────────────────┘

                              ┌──────────────┐
                         ──▶  │    Normal    │  ──▶ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┐
                              │ "normal"     │                            │
                              │ Main agent   │  Query loop                │
                              │ loop         │  completes                 │
                              └──────┬───────┘         │                  │
                                     │                  ▼                  │
                         Conductor/  │         ┌───────────────┐          │
                         TaskCenter  │         │   PostRun     │          │
                         spawns      │         │ "post_run"    │  ──▶  [*]│
                                     │         │ After query   │          │
                                     │         │ ends          │          │
                                     │         └───────────────┘          │
                                     ▼                                     │
                         ┌───────────────────────┐                        │
                         │   ExternalTrigger     │                        │
                         │ "external_trigger"    │  ──▶  [*]  ◀ ─ ─ ─ ─ ┘
                         │ Mid-run ephemeral     │
                         │ agent                 │
                         └───────────────────────┘

  ┌──────────────┬─────────────────────────────────────────────────────────┐
  │  Phase       │  Notes                                                  │
  ├──────────────┼─────────────────────────────────────────────────────────┤
  │ Normal       │ Available during agent work.                            │
  │              │ Tools: read_file, run_command, etc.                     │
  ├──────────────┼─────────────────────────────────────────────────────────┤
  │ PostRun      │ Available after query loop.                             │
  │              │ Tools: submit_plan, post_note,                          │
  │              │        request_replan, declare_blocker.                 │
  ├──────────────┼─────────────────────────────────────────────────────────┤
  │ External     │ Frozen conversation snapshot.                           │
  │ Trigger      │ Separate ephemeral agent.                               │
  │              │ Tools: pause_verdict, post_note.                        │
  └──────────────┴─────────────────────────────────────────────────────────┘
```

A tool's `tool_types` attribute is a `frozenset[ToolType]`; tools can belong to multiple phases. For example, `PostNoteTool` has `tool_types = frozenset({"post_run", "external_trigger"})`.

---

## Post-Run Phase

After the main query loop completes, the `Executor` invokes the post-run phase via `_run_post_run()`. The agent is re-prompted with role-specific posthook tools and must call exactly one to submit its work.

```
  Agent         Executor        Runner          LLM           TaskCenter
(query loop)                  runner.run()
    │               │               │              │               │
    │ query loop    │               │              │               │
    │ returns       │               │              │               │
    │──────────────▶│               │              │               │
    │               │               │              │               │
    │               │ get PosthookTools             │               │
    │               │ by agent role │              │               │
    │               │◀─────────────▶│              │               │
    │               │               │              │               │
    │    [No posthook tools]         │              │               │
    │               │ complete_task(AgentResult)    │               │
    │               │──────────────────────────────────────────────▶│
    │               │               │              │               │
    │    [Has posthook tools]        │              │               │
    │               │ run_trigger(messages,         │               │
    │               │  posthook_tools)              │               │
    │               │──────────────▶│              │               │
    │               │               │              │               │
    │               │   ┌───────────────────────────────────────┐  │
    │               │   │  Loop: until valid tool call (max 10) │  │
    │               │   │                                       │  │
    │               │   │           │ stream_message(           │  │
    │               │   │           │  tool_choice="any")       │  │
    │               │   │           │─────────────────────────▶ │  │
    │               │   │           │              │            │  │
    │               │   │           │ tool_use block            │  │
    │               │   │           │◀─────────────────────────┤  │
    │               │   │           │              │            │  │
    │               │   │           │ pydantic validation       │  │
    │               │   │           │◀─────────────▶            │  │
    │               │   │           │              │            │  │
    │               │   │  [Validation fails]       │            │  │
    │               │   │           │ tool_result with error    │  │
    │               │   │           │─────────────────────────▶ │  │
    │               │   │           │              │            │  │
    │               │   │  [Validation succeeds → execute tool] │  │
    │               │   │           │              │            │  │
    │               │   │    [Tool execution succeeds]          │  │
    │               │   │           │ RunResult ── break        │  │
    │               │   │           │◀──────────────            │  │
    │               │   │    [Tool execution fails]             │  │
    │               │   │           │ tool_result with error    │  │
    │               │   │           │─────────────────────────▶ │  │
    │               │   └───────────────────────────────────────┘  │
    │               │               │              │               │
    │               │ map RunResult → domain object │               │
    │               │◀──────────────│              │               │
    │               │               │              │               │
    │               │ complete_task(result)         │               │
    │               │──────────────────────────────────────────────▶│
    │               │               │              │               │
```

**PosthookTools role mapping:**

- **planner**: `SubmitPlanTool` only
- **replanner**: `AddTasksTool`, `DeclareBlockerTool`, `CancelAndRedraftTool`
- **resolver**: `PostNoteTool`, `RequestReplanTool`
- **explorer**: `PostNoteTool`
- **default**: `PostNoteTool`, `RequestReplanTool`

Post-run tools use `runner.run()` with `execute_tools=True`, meaning tool execution happens inside the loop. Validation errors and tool errors are fed back as `tool_result` blocks so the LLM can retry.

---

## External-Trigger Phase

Mid-run, the Conductor and TaskCenter may spawn ephemeral agents with a frozen conversation snapshot. These agents assess impact (pause verdict) or generate progress notes without interrupting the main task.

```
  Conductor /      Ephemeral        Runner           LLM         Running
  TaskCenter        Agent         runner.run()                     Task
      │               │               │               │              │
      │               │               │               │              │
      │◀──────────────────────────────────────────────────────────── │
      │  blocker declared / checkpoint triggered                      │
      │               │               │               │              │
      │ spawn with    │               │               │              │
      │ frozen        │               │               │              │
      │ snapshot      │               │               │              │
      │──────────────▶│               │               │              │
      │               │               │               │              │
      │               │ run(messages=snapshot,         │              │
      │               │  tools=[pause_verdict|post_note])             │
      │               │──────────────▶│               │              │
      │               │               │               │              │
      │               │   ┌───────────────────────────────────────┐  │
      │               │   │  Loop: until valid tool call (max 10) │  │
      │               │   │                                       │  │
      │               │   │           │ stream_message(           │  │
      │               │   │           │  tool_choice="any")       │  │
      │               │   │           │──────────────────────────▶│  │
      │               │   │           │               │           │  │
      │               │   │           │ tool_use block            │  │
      │               │   │           │◀──────────────────────────│  │
      │               │   │           │               │           │  │
      │               │   │           │ pydantic validation       │  │
      │               │   │           │◀─────────────▶            │  │
      │               │   │           │               │           │  │
      │               │   │  [Validation fails]        │           │  │
      │               │   │           │ tool_result with error    │  │
      │               │   │           │──────────────────────────▶│  │
      │               │   │           │               │           │  │
      │               │   │  [Validation succeeds]     │           │  │
      │               │   │           │ RunResult ── break        │  │
      │               │   │           │◀──────────────            │  │
      │               │   └───────────────────────────────────────┘  │
      │               │               │               │              │
      │               │ RunResult     │               │              │
      │               │◀──────────────│               │              │
      │               │               │               │              │
      │ RunResult with assessment      │               │              │
      │◀──────────────│               │               │              │
      │               │               │               │              │
      │ pause / post note             │               │              │
      │ (task continues unaware)      │               │ ─ ─ ─ ─ ─ ─▶│
      │               │               │               │              │
```

Two external-trigger use cases:

1. **Pause assessment** (`pause_assessment.py`): When a blocker is declared, ephemeral agents assess each running sibling. The agent is given only `PauseVerdictTool` and must respond YES or NO to: "Does your task depend on these broken files?"

2. **Checkpoint notes** (`tc_note.py`): TaskCenter spawns an ephemeral agent with the task's conversation snapshot and `PostNoteTool`. The agent summarizes progress (files edited, current status, suspected blockers) without being noticed by the main task loop.

External-trigger runners use `execute_tools=False` by default. The LLM is constrained to call exactly one tool, and `runner.run()` captures the validated tool input in the `RunResult`. The calling code (Conductor, TaskCenter) then interprets the result.

---

## Shared Runner Loop & Retry Logic

Both post-run and external-trigger phases use the same `runner.run()` loop defined in `external_trigger/runner.py`. The loop guarantees a valid tool call via Pydantic validation retry.

```
  ┌─────────────────┐
  │  runner.run()   │
  └────────┬────────┘
           │
           ▼
  ┌─────────────────────────────────────┐
  │  Prepare ApiMessageRequest          │
  │  tool_choice: auto or exact         │
  └────────┬────────────────────────────┘
           │
           ▼
  ┌─────────────────────────────────────┐ ◀─────────────────────────────────┐
  │  Turn loop (max 10 turns)           │                                   │
  └────────┬────────────────────────────┘                                   │
           │                                                                 │
           ▼                                                                 │
  ┌─────────────────────────────────────┐                                   │
  │  Call LLM — stream_message          │                                   │
  └────────┬────────────────────────────┘                                   │
           │                                                                 │
           ▼                                                                 │
  ┌─────────────────────────────────────┐                                   │
  │  Extract tool_use block             │                                   │
  └────────┬────────────────────────────┘                                   │
           │                                                                 │
           ▼                                                                 │
  ┌─────────────────────┐   No   ┌──────────────────────────────────────┐  │
  │  Tool in registry?  │───────▶│  Post tool_result error              │──┘
  └────────┬────────────┘        │  Append to conversation              │
           │ Yes                 └──────────────────────────────────────┘
           ▼
  ┌─────────────────────┐   No   ┌──────────────────────────────────────┐  │
  │  Pydantic validates?│───────▶│  Post tool_result error              │──┘
  └────────┬────────────┘        │  Append to conversation              │
           │ Yes                 └──────────────────────────────────────┘
           │
           ▼
  ┌─────────────────────┐
  │  execute_tools?     │
  └──────┬──────────────┘
         │                  No
         │─────────────────────────────▶ ┌───────────────────────────────┐
         │                               │  Return RunResult             │
         │ Yes                           │  with validated input         │
         ▼                               └───────────────────────────────┘
  ┌─────────────────────────────────────┐              ▲
  │  Execute tool in context            │              │
  └────────┬────────────────────────────┘              │
           │                                           │
           ▼                                           │
  ┌─────────────────────┐  Success                    │
  │  Tool success?      │────────────────────────────▶┘
  └──────┬──────────────┘
         │ Error
         ▼
  ┌──────────────────────────────────────┐
  │  Post tool_result error              │──▶  (back to Turn loop)
  └──────────────────────────────────────┘

  [Max turns reached]
         │
         ▼
  ┌──────────────────────────────────────┐
  │  Raise RuntimeError (exhausted)      │
  └──────────────────────────────────────┘
```

**Loop behavior:**

- `max_turns` (default 10): Retry limit. Exhaustion raises `RuntimeError`.
- `tool_choice`: Single tool → `{"type": "tool", "name": "..."}`. Multiple tools → `{"type": "any"}`.
- Validation and execution errors are non-fatal; they are appended to the frozen conversation and the loop retries.
- Once a tool call validates (and succeeds if `execute_tools=True`), the loop exits and returns `RunResult`.

`RunResult` captures the tool name, raw input dict, validated Pydantic model, optional tool execution result, and full conversation trail.

---

## Use Sites

### Executor: Post-run submission

**Location:** `backend/src/team/runtime/executor.py:_run_post_run()`

After the agent's query loop completes, the executor re-prompts with posthook tools. The agent's `ToolExecutionContext` carries task metadata (task_center, work_item_id, agent_name, write_scope). The runner executes tools immediately and maps the result:

- `post_note` → `AgentResult(summary=note_content)`
- `submit_plan` → `AgentResult(submitted_plan=plan)` (roster-resolved)
- `request_replan` → `ReplanRequest`
- `declare_blocker` → `BlockerDeclaration`

The result is passed to `_dispatch()`, which routes to `TaskCenter.complete_task()`, `request_replan()`, or triggers conductor blocker mechanics.

### Conductor: Pause assessment (external_trigger)

**Location:** `backend/src/external_trigger/pause_assessment.py:assess_pause()`

When a blocker is declared, the Conductor spawns an ephemeral agent for each running sibling task. The agent receives the task's conversation snapshot and the single `PauseVerdictTool`. The Conductor collects verdicts (YES/NO) and pauses all YES tasks.

Example prompt injected: "A shared dependency has been reported broken. Does your task depend on [files]? Call pause_verdict with your assessment."

The runner does not execute the tool; it returns the validated input only. The Conductor interprets the `PauseVerdictInput.answer` ("YES" or "NO").

### TaskCenter: Checkpoint notes (external_trigger)

**Location:** `backend/src/external_trigger/tc_note.py:run_checkpoint_note()`

TaskCenter active mode monitors edit and turn counters. When a threshold is crossed (5 edits or 10 turns since last note), TaskCenter spawns an ephemeral agent with the task's conversation snapshot and `PostNoteTool`.

Two prompts are available:

- `EDIT_CHECKPOINT_PROMPT`: "What files were edited and why?"
- `TURN_CHECKPOINT_PROMPT`: "Status, findings, and blockers?"

The runner captures the validated `PostNoteInput.content` and TaskCenter posts it as an auto-generated note (`agent_name + " (auto)"`).

---

## Key Design Points

1. **Frozen snapshot invariant:** External-trigger agents receive a read-only conversation snapshot. The original task is never interrupted.

2. **Tool validation guarantee:** The `runner.run()` loop retries up to `max_turns` until Pydantic validation succeeds. It raises `RuntimeError` only if exhausted, never returns invalid input.

3. **Execution context separation:** Post-run tools and external-trigger tools receive the same `ToolExecutionContext` structure but with different metadata (task metadata for post-run, task ID and trigger type for external-trigger).

4. **Roster resolution:** `PosthookTools` resolves agent names and role hints (e.g., "developer" → actual agent name) via `_resolve_agent_name()`. Plans and replans are validated and roster-resolved before being returned to the executor, avoiding lossy re-parsing.

5. **Tool type filtering:** `ToolRegistry.filter_by_type()` returns a new registry containing only tools matching a given `ToolType`. This enables strict phase separation.
