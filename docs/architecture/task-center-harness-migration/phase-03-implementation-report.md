# Phase 03 - Implementation Report

Companion to
[`phase-03-implementation-plan.md`](./phase-03-implementation-plan.md) and
[`phase-03-agent-roles-and-tool-gates.md`](./phase-03-agent-roles-and-tool-gates.md).
This report records what was actually delivered, the verification outcome, the
runtime workflow, and the implementation review findings.

---

## 1. Review verdict

**Verdict: request changes before treating Phase 03 as complete.**

The implementation delivers most of the public submission-tool layer, hard
prehook gates, soft notification triggers, agent-frontmatter wiring, helper
terminals, and targeted tests. The focused test suite is green.

Three implementation issues remain:

| Severity | Finding | Impact |
| --- | --- | --- |
| Major | `request_complex_task_solution` implements delegated request creation and parent resume behavior even though the Phase 03 plan explicitly defers that body to Phase 04 | Phase boundary is no longer coherent, and a failure during delegated graph startup can leave the parent generator task in `waiting_complex_task` after the tool returns an inline error |
| Major | Generator terminals are hard-gated only by structural `generator` role, not by executor vs verifier agent profile | A verifier task can be accepted through `submit_execution_success`, bypassing verifier-only resolver-limit gates if the tool is exposed or misconfigured |
| Major | Planner schemas use `min_length=1` without stripping for task ids, agent names, and partial continuation goals | Whitespace-only ids or continuation goals pass validation and can mutate graph state despite the plan's nonblank-input requirement |

---

## 2. File inventory

### Runtime metadata and tool execution plumbing

| File | Status | Purpose |
| --- | --- | --- |
| `backend/src/engine/core/query.py` | edited | Adds `QueryContext.task_center_task_id` and copies conversation history into tool execution metadata |
| `backend/src/engine/runtime/lifecycle.py` | edited | Stamps `task_center_task_id` into spawned agent query/tool metadata |
| `backend/src/engine/runtime/agent.py` | edited | Stamps agent profile metadata and resolves declarative notification trigger ids |
| `backend/src/tools/core/runtime.py` | edited | Adds typed TaskCenter and conversation metadata to `ExecutionMetadata` |
| `backend/src/tools/core/tool_execution.py` | edited | Copies TaskCenter task id and per-call conversation messages into foreground tool contexts |
| `backend/src/tools/core/factory.py` | edited | Registers the Phase 03 submission tools globally |
| `backend/src/task_center/harness_graph/runtime.py` | edited | Exposes `graph_store`; also adds Phase-04-adjacent manager registry and lifecycle config dependencies |
| `backend/src/task_center/harness_graph/orchestrator.py` | edited | Adds `apply_complex_task_close_report`, which is Phase-04-adjacent resume behavior |

### Submission context, registration, and gates

| File | Status | Purpose |
| --- | --- | --- |
| `backend/src/tools/submission/context.py` | new | Resolves current task id to task, graph, segment, request, runtime, and active orchestrator |
| `backend/src/tools/submission/factory.py` | new | Central factory for all public submission tools |
| `backend/src/tools/submission/hooks/harness_role_gate.py` | new | Structural role, graph, and active-orchestrator gate |
| `backend/src/tools/submission/hooks/recursive_partial_plan_gate.py` | new | Blocks partial plans after prior segment continuation |
| `backend/src/tools/submission/hooks/request_complex_task_before_edit_gate.py` | new | Blocks executor request start after edit-capable tool use |
| `backend/src/tools/submission/hooks/resolver_success_limit_gate.py` | new | Blocks verifier/evaluator success at unresolved resolver limit |
| `backend/src/tools/submission/hooks/helper_request_gate.py` | new | Guards helper request tools by caller profile role |
| `backend/src/tools/submission/hooks/helper_role_gate.py` | new | Guards helper/subagent terminal tools by helper metadata |

### Soft notification triggers

| File | Status | Purpose |
| --- | --- | --- |
| `backend/src/tools/submission/notification_triggers/__init__.py` | new | Maps stable trigger ids to `NotificationRule` factories |
| `backend/src/tools/submission/notification_triggers/recursive_partial_plan.py` | new | Planner reminder when partial planning is disabled |
| `backend/src/tools/submission/notification_triggers/request_complex_task_after_edit.py` | new | Executor reminder after edits disable request start |
| `backend/src/tools/submission/notification_triggers/resolver_limit.py` | new | Verifier/evaluator warning before resolver success is blocked |

### Public tool surface

| Area | Status | Delivered tools |
| --- | --- | --- |
| Planner | implemented | `submit_full_plan`, `submit_partial_plan`, planner schemas and normalization |
| Generator executor | implemented | `submit_execution_success`, `submit_execution_failure`, `request_complex_task_solution` |
| Generator verifier | implemented | `submit_verification_success`, `submit_verification_failure` |
| Evaluator | implemented | `submit_evaluation_success`, `submit_evaluation_failure` |
| Advisor helper | implemented | `ask_advisor`, `submit_advisor_feedback` |
| Resolver helper | implemented | `ask_resolver`, `submit_resolver_result` |
| Explorer subagent | implemented | `submit_exploration_result` |
| Legacy executor request-plan surface | removed | `submit_request_plan` is no longer registered |

### Agent definitions and tests

| File / group | Status | Purpose |
| --- | --- | --- |
| `backend/src/agents/types.py` | edited | Adds `notification_triggers` frontmatter support |
| `backend/src/agents/main_agent/**/agent.md` | edited | Declares role-specific terminals and soft trigger ids |
| `backend/src/agents/helper_agent/**/agent.md` | edited | Aligns helper terminal contracts |
| `backend/src/agents/subagent/explorer/agent.md` | edited | Aligns explorer terminal contract |
| `backend/tests/test_tools/test_submission_*.py` | new | Registration, planner, gates, routing, helpers, and reminder coverage |
| `backend/tests/task_center/lifecycle/test_phase03_submission_integration.py` | new | Tool-driven graph smoke coverage |
| `backend/tests/test_agents/test_agent_markdown.py` | new | Agent markdown trigger and request-start contract coverage |

---

## 3. Lines of code

Current line counts for the main participating Phase 03 files:

| Bucket | Files | Lines |
| --- | ---: | ---: |
| Submission package, including tools, hooks, triggers, and context | 40 | 1,976 |
| Runtime/core/harness files touched for metadata and routing | 8 | 2,139 |
| Agent definition model and bundled role prompts | 8 | 321 |
| Phase 03-focused tests and shared fixtures | 10 | 1,389 |
| **Total participating files** | **66** | **5,825** |

---

## 4. Test outcome

Commands run during review:

- `uv run pytest backend/tests/test_tools/test_submission_tool_registration.py backend/tests/test_tools/test_submission_planner_tools.py backend/tests/test_tools/test_submission_tool_gates.py backend/tests/test_tools/test_submission_terminal_routing.py backend/tests/test_tools/test_submission_helper_tools.py backend/tests/test_tools/test_submission_soft_reminders.py backend/tests/task_center/lifecycle/test_phase03_submission_integration.py backend/tests/test_agents/test_agent_markdown.py -q`: **38 passed**
- `uv run ruff check backend/src/tools/submission backend/src/tools/core backend/src/engine/core backend/src/engine/runtime backend/src/task_center backend/tests/test_tools backend/tests/task_center/lifecycle/test_phase03_submission_integration.py backend/tests/test_agents/test_agent_markdown.py`: clean
- `uv run mypy --config-file backend/mypy.ini backend/src/task_center backend/src/agents`: clean
- `uv run pytest backend/tests/task_center/lifecycle/test_harness_graph_orchestrator.py backend/tests/task_center/lifecycle/test_integration_phase02.py -q`: **18 passed**
- `uv run pytest backend/tests/test_tools/test_tool_execution.py backend/tests/test_tools/test_schema_summary.py -q`: **33 passed**
- `uv run pytest backend/tests/test_agents/test_agent_markdown.py -q`: **2 passed**
- `uv run pytest backend/tests/task_center -q`: **107 passed**
- `git diff --check`: clean

Exit criteria mapping:

| Exit criterion | Current coverage |
| --- | --- |
| Every terminal or orchestration request is accepted or rejected from the new state model | Role-gate tests and terminal routing tests |
| Recursive partial plan is blocked across `TaskSegment` lineage | `test_recursive_partial_plan_gate_blocks_after_prior_continuation` |
| `request_complex_task_solution` is blocked after executor edits | `test_request_complex_task_solution_blocks_after_edit` |
| Resolver unresolved-count gates force failure at the limit | `test_resolver_success_gate_boundary_and_limit` plus failure-terminal coverage |
| Malformed plans fail inline without marking graph failed | `test_plan_validation_errors_do_not_mutate_graph` |
| Accepted planner submissions persist graph contract through orchestrator | Full/partial planner routing tests |
| Accepted generator and evaluator terminals use Phase 02 apply surface | Terminal routing and integration smoke tests |
| Helper-agent request tools return helper terminal results without graph mutation | Helper tool tests |
| Explorer subagent terminal result is returned through `run_subagent` | Explorer terminal metadata test |
| Soft reminders match representative hard-gate conditions | Soft reminder tests |
| Executor public contract uses `request_complex_task_solution` | Agent markdown and registration tests |

Missing coverage relative to the plan:

- No regression test rejects whitespace-only `continuation_goal`, task ids, or agent names.
- No hard-gate test proves executor terminals reject verifier-owned generator tasks, or verifier terminals reject executor-owned generator tasks.
- No failure-path test proves `request_complex_task_solution` leaves parent generator state unchanged when delegated graph startup fails.

---

## 5. Runtime workflow now implemented

Phase 03 now routes accepted terminal calls through the graph state model:

```text
Harness agent run
  |
  v
QueryContext.task_center_task_id + ExecutionMetadata.harness_graph_runtime
  |
  v
Tool prehooks
  - HarnessRoleGate
  - RecursivePartialPlanGate
  - RequestComplexTaskBeforeEditGate
  - ResolverSuccessLimitGate
  - HelperRequestGate / HelperRoleGate
  |
  v
Submission context resolver
  task row -> harness graph -> task segment -> complex request -> orchestrator
  |
  v
Terminal handler
  planner -> PlannerSubmission -> apply_plan_submission
  generator -> GeneratorSubmission -> apply_generator_submission
  evaluator -> EvaluatorSubmission -> apply_evaluator_submission
  helper/subagent -> terminal ToolResult returned to caller
  |
  v
Successful terminal ToolResult
  query loop stamps does_terminate=True and stops the run
```

The soft layer is also wired:

```text
AgentDefinition.notification_triggers
  |
  v
spawn_agent resolves ids through tools.submission.notification_triggers
  |
  v
notification.rules.dispatch_rules evaluates each NotificationRule per turn
  |
  v
SystemNotificationService emits ordinary <system-reminder> messages
```

The implementation also adds a Phase-04-adjacent delegated request-start path:

```text
request_complex_task_solution
  |
  v
create delegated ComplexTaskRequest
  |
  v
create initial TaskSegment and TaskSegmentManager
  |
  v
mark parent generator task waiting_complex_task
  |
  v
create initial delegated HarnessGraph and start its orchestrator
  |
  v
complex task close report -> parent orchestrator.apply_complex_task_close_report
```

That path is functional in targeted tests, but it is outside the Phase 03 plan
boundary and needs the same Phase 04 review and recovery semantics as the rest
of complex-task spawning.

---

## 6. State invariants enforced now

- Submission tools require injected TaskCenter metadata and an active
  process-local orchestrator.
- Optional graph metadata is checked against the persisted task row.
- Planner/generator/evaluator submissions resolve graph identity from the
  current task row rather than trusting caller-supplied graph ids.
- Planner handlers validate duplicate task ids, unknown generator agents,
  exact `task_specs` coverage, blank task spec values, dangling dependencies,
  and dependency cycles before calling the orchestrator.
- `submit_partial_plan` is prehook-blocked when an earlier segment in the same
  request already has a `continuation_goal`.
- `request_complex_task_solution` is prehook-blocked after edit-capable tool
  use in the current executor run.
- Resolver-limit gates attach only to verifier/evaluator success terminals;
  failure terminals remain available.
- Helper request tools are non-terminal and dispatch blocking helper agents.
- Helper and explorer terminal tools are terminal only for their helper runs.
- Soft reminders use the existing notification-rule system rather than a second
  prompt or hook framework.
- Legacy `submit_request_plan` is not registered.

---

## 7. Review findings

### 7a. Phase 04 request-start behavior shipped inside Phase 03

`request_complex_task_solution` now creates delegated complex-task requests,
segments, managers, graphs, and parent resume behavior. The Phase 03 plan
explicitly says this body belongs to Phase 04 and that Phase 03 should return an
inline "not wired" error unless a handler is supplied.

Evidence:

- `backend/src/tools/submission/main_agent/generator/request_complex_task_solution.py:110`
  starts the built-in delegated request path when no injected handler is present.
- `backend/src/tools/submission/main_agent/generator/request_complex_task_solution.py:134`
  marks the parent task `waiting_complex_task` before delegated graph startup.
- `backend/src/task_center/harness_graph/orchestrator.py:199` adds parent
  resume behavior through `apply_complex_task_close_report`.

This is more than scope drift. In the current handler, the parent generator task
is marked `waiting_complex_task` before the delegated graph is created. If delegated
graph creation or orchestrator startup raises, the tool returns an inline error
while the parent task may already be waiting and a delegated request/segment may
already exist.

Recommended fix:

- Either move this body back behind an injected Phase 04 handler and restore the
  explicit unwired error for Phase 03, or update the plan/report boundary to
  make this a Phase 04 implementation.
- If the body stays, make request creation, parent waiting status, and delegated
  graph startup transactional or compensating, and add a regression test for
  startup failure after parent status mutation.

### 7b. Generator subrole terminals are not hard-gated

The hard gate checks only persisted `role == "generator"`. It does not verify
whether the current generator task was launched as `executor` or `verifier`.
That means a verifier-owned generator task can be accepted by
`submit_execution_success`, which bypasses the verifier success resolver-limit
gate and stores an executor payload.

Evidence:

- `backend/src/tools/submission/hooks/harness_role_gate.py:34` compares only
  the persisted structural role.
- `backend/src/tools/submission/main_agent/generator/executor/submit_execution_success.py:30`
  attaches only `HarnessRoleGate(..., HarnessTaskRole.GENERATOR)`.
- `backend/src/tools/submission/main_agent/generator/verifier/submit_verification_success.py:31`
  attaches the same structural gate plus the resolver-limit gate, leaving the
  executor success terminal as a bypass when misexposed.

Agent definitions normally hide the wrong terminal from each role, but Phase 03
is specifically the hard gate layer. It should not rely on prompt/tool-surface
configuration alone for executor vs verifier semantics.

Recommended fix:

- Persist generator agent profile on task rows or expose a safe orchestrator
  lookup for the planned generator agent name.
- Add a role/profile gate for executor-only and verifier-only generator
  terminals.
- Add tests that executor tasks cannot call verifier terminals and verifier
  tasks cannot call executor terminals.

### 7c. Planner "nonblank" validation accepts whitespace-only values

Planner schemas use `Field(..., min_length=1)` for task ids, agent names, and
partial continuation goals. Pydantic does not strip whitespace for those fields,
so `" "` passes. The plan requires nonblank task ids and blank partial
continuation goals to be rejected before graph mutation.

Evidence:

- `backend/src/tools/submission/main_agent/planner/_schemas.py:19` and
  `backend/src/tools/submission/main_agent/planner/_schemas.py:20` validate
  task id and agent name with `min_length=1` only.
- `backend/src/tools/submission/main_agent/planner/submit_partial_plan.py:25`
  validates `continuation_goal` with `min_length=1` only.
- `backend/tests/test_tools/test_submission_planner_tools.py` covers blank task
  spec values but has no whitespace-only task id, agent name, or continuation
  goal regression test.

Recommended fix:

- Use constrained strings with stripping, field validators, or explicit
  `.strip()` checks for `PlanTaskInput.id`, `PlanTaskInput.agent_name`,
  `SubmitPartialPlanInput.continuation_goal`, and any other public text fields
  that are treated as nonblank contracts.
- Add the planned `test_partial_plan_rejects_blank_continuation_goal` plus task
  id / agent name whitespace cases.

---

## 8. What's deferred

| Item | Where | Phase |
| --- | --- | --- |
| Durable restart recovery for active orchestrator lookup | `HarnessGraphOrchestratorRegistry` / cutover runtime | 05 |
| Production launch metadata wiring beyond direct `run_ephemeral_agent` stamping | real `HarnessAgentLauncher` implementation | 05 |
| Rich helper-agent context packets | advisor/resolver request prompt construction | 06 |
| Context-engine summaries and durable graph summaries | launch packets and close payloads | 06 |
| Full delegated complex-task spawning and request-start review, if the current early implementation remains | `request_complex_task_solution`, `apply_complex_task_close_report` | 04 |
