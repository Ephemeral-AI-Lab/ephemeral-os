# Phase 03 - Agent Roles and Tool Gates

## Goal

Port role semantics and terminal-tool gating onto the new
`Mission` / `Episode` / `Attempt` state model.

This phase should preserve the public agent contract while changing where state
is read from.

## Phase 01 inheritance

Phase 01 ships the durable state model and the request/segment lifecycle
that every gate reads from; Phase 03 wires the tool-side enforcement.

**Already in place:**

- Three persisted records (`Mission`, `Episode`,
  `Attempt`) and their stores in `db.stores`. Tool prehooks read
  structural state through `MissionStore.get` /
  `list_for_executor_task`, `EpisodeStore.get` / `list_for_request` /
  `get_by_sequence`, and `AttemptStore.get` / `list_for_episode` /
  `get_by_sequence`.
- `Episode.continuation_goal` is set only from passing graphs — enforced
  by `assert_continuation_goal_only_from_passing_graph`. The partial-plan
  ancestor gate can walk from `Mission.requested_by_task_id` to the
  caller task's `task_center_attempt_id`, inspect that graph's
  `continuation_goal`, and repeat through caller request ancestry.
- `'root'` creation reason is rejected by `assert_no_root_creation_reason`.
- `get_attempt_count(episode)` (under
  `task_center.mission.segment.attempt_count`) returns the
  budget-remaining input for the next-graph gate.
- `MissionHandler.create_initial_segment` and
  `create_continuation_segment` enforce the segment-side half of the
  partial-plan-continuation gate (predecessor SUCCEEDED + non-null
  `continuation_goal`); only the planner-side `submit_partial_plan`
  prehook is missing.
- `AttemptStore.set_plan_contract(task_specification,
  evaluation_criteria, continuation_goal)` and `set_planner_task_id` are
  the persistence calls the planner-submission handlers will make.

**Phase 03 wires:**

- `submit_full_plan` and `submit_partial_plan` handlers call
  `AttemptStore.set_plan_contract` and `set_planner_task_id` on the
  active graph and validate the generator graph (task-id uniqueness, agent
  names, exact `task_specs` coverage, dependency validity).
- The harness launcher stamps `QueryContext.task_center_task_id` and
  `ExecutionMetadata.task_center_task_id` when spawning planner, generator,
  and evaluator agents. Tool handlers and prehooks use that task id as the
  trusted runtime identity.
- Tool prehooks resolve request, segment, graph, task, and orchestrator state
  from the current task id plus `AttemptRuntime`; message-history gates
  read the existing query-loop message flow.
- `request_mission_solution` after-edit gating reads the per-call
  `ExecutionMetadata.conversation_messages` view; no Phase 01 dependency.
  Its request-start body creates a request via
  `MissionHandler.create_mission` plus
  `create_initial_segment`. The Phase 04 request-start flow described in
  `phase-04-mission-spawning.md` shares this entry point.

## Role model

TaskCenter owns four main agent roles, all scoped to one `Attempt` except
the requesting generator task, whose task result can be supplied by a delegated
`Mission` close report.

| Role | Scope | Tools / terminals |
| ---- | ----- | ----------------- |
| Planner | one `Attempt` | `submit_full_plan`, `submit_partial_plan` |
| Generator direct-work profile | one `Attempt` DAG node | `submit_execution_success`, `submit_execution_failure`; may also declare `request_mission_solution` |
| Generator verifier | one `Attempt` DAG node | `submit_verification_success`, `submit_verification_failure` |
| Evaluator | sink for one `Attempt` | `submit_evaluation_success`, `submit_evaluation_failure` |

Planner has no failure terminal. Executor, verifier, and evaluator are the roles
that can declare failure.

`request_mission_solution` is not a terminal failure. It starts a delegated
complex-task request: a generator task delegates its task to a separate
complex-task workflow, and the delegated request's close report becomes that
generator task result.

A generator agent profile can expose `request_mission_solution` when the
runtime provides the TaskCenter request-start dependencies. The default
request-start path creates the delegated request, marks the outer generator task
waiting, and routes the mission close report back to that generator task.

## Planner terminal signatures

Planner submissions must define the segment contract directly.

```python
submit_full_plan(
    task_specification: str,
    evaluation_criteria: list[str],
    tasks: list[{"id": str, "agent_name": str, "deps": list[str]}],
    task_specs: dict[str, str],
) -> TerminalSubmission
```

```python
submit_partial_plan(
    task_specification: str,
    evaluation_criteria: list[str],
    tasks: list[{"id": str, "agent_name": str, "deps": list[str]}],
    task_specs: dict[str, str],
    continuation_goal: str,
) -> TerminalSubmission
```

Each `tasks` item is a flat graph node with exactly `id`, `agent_name`, and
`deps`. `task_specs` maps each task id to that task's detailed instructions.
The keys in `task_specs` must exactly match the task ids in `tasks`: no missing
specs, no extra specs, and no duplicate task ids.

`task_specification` describes the exact work for the current segment.
`evaluation_criteria` lists the pass/fail conditions the evaluator must use to
evaluate this segment's result. `AttemptOrchestrator` passes both fields to
the evaluator as evaluation instructions. For `submit_partial_plan`,
`continuation_goal` describes what the next segment should solve if this graph
is accepted as the segment's closing graph.

## Helper roles

| Helper | Entry point | Blocking | Edit authority | TaskCenter node? |
| ------ | ----------- | -------- | -------------- | ---------------- |
| Explorer | `run_subagent(name="explorer", prompt)` | no | read-only | no |
| Advisor | `ask_advisor(tool_name, tool_payloads, prompt)` | yes | no edits | no |
| Resolver | `ask_resolver(issues_to_resolve)` | yes | may edit | no |

Resolver is called by a verifier or evaluator when it finds issues it cannot
resolve through read-only checks. It returns `resolved` plus summaries to the
calling task.

## State-dependent tool policy

Tool availability depends on:

- mission request origin,
- task segment continuation chain,
- current harness graph,
- task role,
- task message/tool history.

The runtime composes two layers:

- Soft layer: existing `notification.rules` / `notification.service`
  system-reminder plumbing injects currently relevant constraints.
- Hard layer: existing `tools.core.hooks` prehooks, executed by
  `ToolHookExecutionHelper`, enforce the same constraints before handlers run.

Neither layer mutates the system prompt, dynamically changes tool registration,
or introduces a second hook/reminder framework.

## Runtime identity and submission context

Every harness agent run has one persisted TaskCenter task row. The production
launcher passes that row id as `task_id` to `run_ephemeral_agent(...)`; Phase 03
threads it into:

```python
QueryContext.task_center_task_id
ExecutionMetadata.task_center_task_id
```

Tools read this value from `ToolExecutionContextService`. This is the primary
identity for terminal submissions and gate checks. The runtime does not need to
trust an independently injected graph id.

Graph id is still needed internally, but it is derived:

```text
task_center_task_id
  -> AttemptRuntime.task_store.get_task(task_id)
  -> task["task_center_attempt_id"]
  -> AttemptRuntime.graph_store.get(graph_id)
  -> segment_store.get(graph.episode_id)
  -> request_store.get(segment.mission_id)
  -> orchestrator_registry.get(graph_id)
```

`task_center_attempt_id` may be carried in metadata as an optional
consistency check, but the persisted task row is authoritative.

Phase 03 should add one internal submission-context resolver under
`tools/submission/` so handlers and gates do not each repeat this lookup. The
resolver returns the current task row, graph, segment, request, runtime, and
active orchestrator. To support this without private orchestrator access,
`AttemptRuntime` should expose `graph_store`.

Helper request gates are different from graph submission gates. Graph task rows
only distinguish structural roles (`planner`, `generator`, `evaluator`), while
helper policy needs agent-profile roles such as `executor` and `verifier`.
Therefore helper gates read stamped `ExecutionMetadata.agent_name`, `role`, and
`agent_type`, not only `HarnessTaskRole`.

## Tool gating matrix

| Tool | Block when | State source | Soft behavior | Hard behavior |
| ---- | ---------- | ------------ | ------------- | ------------- |
| `submit_partial_plan` | current request was spawned, directly or transitively, from a harness graph whose planner submitted a partial plan | Walk `Mission.requested_by_task_id` to the caller task, then caller `Attempt.continuation_goal`, then the caller graph's request ancestor chain | remind planner that only `submit_full_plan` is allowed | prehook blocks nested partial planning below a partial-planned ancestor graph |
| `submit_full_plan` malformed generator graph | duplicate task id, unknown agent name, missing or extra task spec, cycle, dangling dependency, or unknown task ref | handler-level validation | none | handler returns `ToolResult(is_error=True, output=reason)` |
| `submit_partial_plan` malformed generator graph | duplicate task id, unknown agent name, missing or extra task spec, cycle, dangling dependency, or unknown task ref, or blank `continuation_goal` | handler-level validation plus continuation validation | none | handler returns `ToolResult(is_error=True, output=reason)` |
| `request_mission_solution` | generator agent has called any edit tool at least once | soft rules inspect their `messages` argument; prehooks inspect `ExecutionMetadata.conversation_messages` | remind generator after first edit | prehook blocks after edit |
| `submit_evaluation_success` | evaluator has at least five unresolved resolver calls | soft rules inspect their `messages` argument; prehooks inspect `ExecutionMetadata.conversation_messages` | warn at four unresolved resolver calls | prehook blocks success at five |
| `submit_verification_success` | verifier has at least five unresolved resolver calls | soft rules inspect their `messages` argument; prehooks inspect `ExecutionMetadata.conversation_messages` | warn at four unresolved resolver calls | prehook blocks success at five |
| evaluator spawn | any generator in current `Attempt` is not `DONE` | current harness graph task statuses | none | `AttemptOrchestrator` does not spawn evaluator |
| next harness graph after failed graph | `get_attempt_count(episode) >= attempt_budget` | current task segment state | none | `EpisodeManager` cannot spend attempt budget on another graph; it closes the segment failed if the current graph failed |
| failure terminals | never blocked for owning roles | role policy | none | allowed |

## Gate enforcement flow

```text
agent calls tool(input)
        |
        v
prehook(tool_input, tool_context)
        |
        +-- reads:
        |     task_center_task_id from tool context
        |     submission context resolver:
        |       task row -> harness graph -> task segment -> request
        |       active orchestrator
        |     structural task role
        |     agent profile role when helper policy needs executor/verifier
        |     ExecutionMetadata.conversation_messages
        |
        +-- ALLOW -> run handler -> MissionHandler,
                       EpisodeManager, or AttemptOrchestrator
                       observes transition
        |
        +-- BLOCK -> ToolResult(is_error=True, output=reason)
                    agent chooses a different path
```

Soft layer examples:

- First edit detected: `request_mission_solution` is now disabled.
- Resolver unresolved count is four: one resolver call remains before success is
  blocked.
- This request has a partial-planned caller graph in its ancestry: only
  `submit_full_plan` is permitted.

## Implementation tasks

1. Add `QueryContext.task_center_task_id` and
   `ExecutionMetadata.task_center_task_id`, and stamp both from
   `run_ephemeral_agent(task_id=...)`.
2. Add `AttemptRuntime.graph_store` and one internal submission-context
   resolver that derives graph, segment, request, and orchestrator from the
   current `task_center_task_id`.
3. Implement the gates as ordinary `ToolPreHook` instances attached through
   `BaseTool.pre_hooks`, reading `Mission`, `Episode`,
   `Attempt`, role, and conversation state.
4. Add malformed generator graph validation to plan submission handlers,
   including task id uniqueness, known agent names, exact `task_specs` coverage,
   and dependency validity.
5. Add partial-plan ancestor gating by walking from the current
   `Mission.requested_by_task_id` to its caller harness graph, then
   repeating through caller request ancestry. Block only when an ancestor caller
   graph has non-null `Attempt.continuation_goal`.
6. Add `request_mission_solution` after-edit gating from
   `ExecutionMetadata.conversation_messages`.
7. Add resolver-count gating for verifier and evaluator success terminals.
8. Keep TaskCenter soft reminder factories aligned with hard prehook behavior
   while dispatching through the existing notification runtime.
9. Add tests for each gate at both notification and enforcement level where
   practical.

## Phase exit criteria

- Every terminal or orchestration request is accepted or rejected from the new
  state model.
- Nested partial planning is blocked below partial-planned ancestor graphs.
- `request_mission_solution` is blocked after generator edits.
- Resolver unresolved-count gates still force failure at the limit.
- Malformed plans fail inline without marking the harness graph failed.
