# Agent Tool Surface System

EphemeralOS runs each agent as one provider request: system prompt, user prompt,
assistant response. There is no same-run continuation prompt after tool results,
so the assistant must choose its terminal submission directly in that response.

## Current Model

`AgentDefinition.modes` contains exactly one default tool surface. That surface
contains:

- regular tools the assistant may call as side effects
- every terminal tool the assistant may use to finish the run

For builtin agents this means:

- executor terminals: `submit_task_completion`, `submit_plan_handoff`
- evaluator terminals: `submit_task_completion`, `submit_continue_work_handoff`
- explorer terminal: `submit_exploration_result`

The old secondary-mode entry flow has been removed. There are no entry tools,
briefing tool results, task mode transitions, or follow-up model requests inside
one agent run.

## Dispatch Rules

Tool gating is enforced during tool dispatch:

1. If `QueryContext.active_mode` is set, the tool must be in that surface's
   `allowed_tools` or `terminals`.
2. Terminal tools are batch-exclusive. They must be the only tool call in the
   assistant response.
3. A successful terminal tool sets `QueryContext.terminal_result` and exits with
   `QueryExitReason.TOOL_STOP`.
4. A rejected tool call is emitted as a tool error event, but it does not create
   a second model request in the same run.

## Prompt Contract

Prompts should present all valid terminal choices up front and require exactly
one terminal submission in the assistant response.

Examples:

- "Use `submit_task_completion` when the task is complete."
- "Use `submit_plan_handoff` when the task needs decomposition."
- "Use `submit_continue_work_handoff` when evaluator feedback requires another
  executor pass."

## Failure Behavior

If an agent response does not include a successful terminal tool, callers treat
the run as non-terminal. Team runtime marks that task failed and uses its normal
failure path. Recovery is modeled as a fresh agent run with a fresh prompt, not
as a continuation inside the same run.
