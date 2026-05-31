# Unified Submission Terminals And Terminal Routing Migration Plan

Date: 2026-05-31

## Goal

Collapse status-split submission terminals into status/payload-driven terminal tools and remove launch-time terminal routing for planner and generator profiles.

The resulting model should be:

- Planner always sees one terminal: `submit_planner_outcome`.
- `submit_planner_outcome` completes the current iteration when `deferred_goal_for_next_iteration` is absent or `null`.
- `submit_planner_outcome` defers concrete remaining current-iteration goal items to the next iteration when `deferred_goal_for_next_iteration` is nonblank.
- Generator sees one status terminal: `submit_generator_outcome(status, outcome)`.
- Reducer sees one status terminal: `submit_reducer_outcome(status, outcome)`.
- Nested planner deferral is rejected by a prehook, not hidden by terminal filtering.
- Generator still sees `submit_workflow_handoff`, but nested handoff is rejected by a prehook.
- One-shot system reminders explain disabled nested behavior immediately after the initial launch messages.
- `TerminalToolRouter` and profile-level `terminal_routing` disappear.

## Target Structure

```text
backend/src/
  agents/
    definition/
      model.py
      loader.py
    profile/main/
      planner.md
      executor.md
      reducer.md

  task_center/
    _core/
      workflow_depth.py
    agent_launch/
      composer.py

  tools/
    _names.py
    _terminals/
      registry.py
    _hooks/
      advisor_approval.py
      require_no_inflight_background_tasks.py
      disallow_nested_planner_deferral.py
      disallow_nested_workflow_handoff.py

    submission/
      _factory.py
      planner/
        __init__.py
        _schemas.py
        _prompt_guidance.py
        submit_planner_outcome/
          __init__.py
          prompt.py
          submit_planner_outcome.py
      generator/
        __init__.py
        _prompt_guidance.py
        submit_generator_outcome/
          __init__.py
          prompt.py
          submit_generator_outcome.py
        submit_workflow_handoff/
          prompt.py
          submit_workflow_handoff.py
      reducer/
        __init__.py
        _prompt_guidance.py
        submit_reducer_outcome/
          __init__.py
          prompt.py
          submit_reducer_outcome.py
      notification_triggers/
        __init__.py
        request_workflow_after_edit.py
        nested_planner_deferral_disabled.py
        nested_workflow_handoff_disabled.py
```

## Files To Add

### `backend/src/task_center/_core/workflow_depth.py`

Move the workflow ancestry calculation out of terminal routing into a neutral TaskCenter helper.

Expected API:

```python
def workflow_depth(*, workflow_id: str, deps: ContextEngineDeps) -> int: ...

def is_nested_workflow(*, workflow_id: str | None, deps: ContextEngineDeps) -> bool: ...
```

Rules:

- Root workflow depth is `1`.
- Delegated child workflow depth is `2`.
- Nested policy gates fire when depth is greater than `1`.
- Cycle or missing-state behavior should preserve the invariant-violation semantics previously owned by `task_center/_core/terminal_routing.py`.

### `backend/src/tools/_hooks/disallow_nested_planner_deferral.py`

Prehook attached to `submit_planner_outcome`.

Behavior:

- Inspect the validated `submit_planner_outcome` input.
- If `deferred_goal_for_next_iteration` is missing, `None`, or blank after trimming, pass.
- Resolve attempt submission context.
- Compute workflow depth.
- If depth is greater than `1`, fail with a clear hook error explaining that nested workflows must close their child workflow scope and cannot defer another iteration.

Suggested ordering on `submit_planner_outcome`:

```python
pre_hooks=(
    RequireNoInflightBackgroundTasks("submit_planner_outcome"),
    DisallowNestedPlannerDeferral("submit_planner_outcome"),
    AdvisorApprovalPreHook("submit_planner_outcome"),
)
```

### `backend/src/tools/_hooks/disallow_nested_workflow_handoff.py`

Prehook attached to `submit_workflow_handoff`.

Behavior:

- Resolve generator submission context.
- Compute workflow depth from the current attempt's workflow.
- If depth is greater than `1`, fail with a clear hook error explaining that nested generators cannot start another delegated workflow and must submit success or failure.

Suggested ordering on `submit_workflow_handoff`:

```python
pre_hooks=(
    RequireNoInflightBackgroundTasks("submit_workflow_handoff"),
    DisallowNestedWorkflowHandoff("submit_workflow_handoff"),
    AdvisorApprovalPreHook("submit_workflow_handoff"),
)
```

### `backend/src/tools/submission/planner/submit_planner_outcome/`

New unified planner terminal.

Files:

- `__init__.py`
- `prompt.py`
- `submit_planner_outcome.py`

Input:

```python
class SubmitPlannerOutcomeInput(SharedPlannerSubmissionInput):
    deferred_goal_for_next_iteration: str | None = Field(
        default=None,
        description=(
            "Concrete goal items from the current iteration goal that this plan "
            "intentionally leaves for the next iteration. Omit or null means this "
            "plan covers all current-iteration goal items and leaves no remaining items."
        ),
    )
```

Semantics:

- `deferred_goal_for_next_iteration is None`: `kind="completes"`.
- `deferred_goal_for_next_iteration.strip()` is nonempty: `kind="defers"`.
- `deferred_goal_for_next_iteration.strip()` is empty: validation error.
- The field is not a speculative backlog or generic "continue work" note. It lists concrete current-iteration goal items intentionally left for the next iteration.

Metadata:

- Preserve current planner metadata shape where useful.
- `submission_kind` should be either `planner_completes` or `planner_defers`.
- Keep `task_center_task_id` and `attempt_id`.

### `backend/src/tools/submission/generator/submit_generator_outcome/`

New unified generator status terminal.

Files:

- `__init__.py`
- `prompt.py`
- `submit_generator_outcome.py`

Input:

```python
class SubmitGeneratorOutcomeInput(BaseModel):
    status: Literal["success", "failed"]
    outcome: str = Field(..., min_length=1)
```

Semantics:

- `status="success"` records generator success.
- `status="failed"` records generator failure.
- `outcome` must include the concrete result, evidence, verification, and artifact references needed by downstream generators or reducers.
- Do not keep a separate `artifacts` payload field unless implementation proves a current caller needs it. Prefer putting artifact references in `outcome`.

Metadata:

- `submission_kind` should be `generator_success` or `generator_failure`.
- Keep `task_center_task_id` and `attempt_id`.

### `backend/src/tools/submission/reducer/submit_reducer_outcome/`

New unified reducer status terminal.

Files:

- `__init__.py`
- `prompt.py`
- `submit_reducer_outcome.py`

Input:

```python
class SubmitReducerOutcomeInput(BaseModel):
    status: Literal["success", "failed"]
    outcome: str = Field(..., min_length=1)
```

Semantics:

- `status="success"` records reducer success.
- `status="failed"` records reducer failure.
- `outcome` must summarize the completed reducer result or the concrete blocker/missing context.

Metadata:

- `submission_kind` should be `reducer_success` or `reducer_failure`.
- Keep `task_center_task_id` and `attempt_id`.

### Notification Triggers

Add:

- `backend/src/tools/submission/notification_triggers/nested_planner_deferral_disabled.py`
- `backend/src/tools/submission/notification_triggers/nested_workflow_handoff_disabled.py`

Register both in:

- `backend/src/tools/submission/notification_triggers/__init__.py`

Planner trigger:

- Name: `nested_planner_deferral_disabled`
- Fires once.
- Fires immediately at run start when workflow depth is greater than `1`.
- Message should say `submit_planner_outcome` is still the planner terminal, but `deferred_goal_for_next_iteration` must be omitted in nested workflows.

Generator trigger:

- Name: `nested_workflow_handoff_disabled`
- Fires once.
- Fires immediately at run start when workflow depth is greater than `1`.
- Message should say `submit_workflow_handoff` is blocked in nested workflows; use `submit_generator_outcome(status="success", outcome=...)` or `submit_generator_outcome(status="failed", outcome=...)`.

The query loop already evaluates notification rules before building each provider request, so these reminders will be appended after the initial launch messages and before the first provider turn.

## Files To Modify

### Planner Tool Schema

File:

- `backend/src/tools/submission/planner/_schemas.py`

Changes:

- Keep `SharedPlannerSubmissionInput`, `PlanTaskInput`, and `ReducerInput`.
- Replace external close/defer split with a helper that normalizes `deferred_goal_for_next_iteration`.
- Define `deferred_goal_for_next_iteration` as concrete current-iteration goal items intentionally deferred to the next iteration.
- Keep `build_planner_submission(...)` as the submission DTO builder.
- Either keep `kind` as an explicit internal argument or add a small helper:

```python
def planner_kind_from_deferred_goal(value: str | None) -> tuple[Literal["completes", "defers"], str | None]:
    ...
```

### Planner Profile

File:

- `backend/src/agents/profile/main/planner.md`

Changes:

- Replace terminals:

```yaml
terminals:
  - submit_planner_outcome
```

- Remove:

```yaml
terminal_routing: planner_routing.py
```

- Add:

```yaml
notification_triggers:
  - nested_planner_deferral_disabled
```

- Rewrite prose from two terminal tools to one terminal with `deferred_goal_for_next_iteration`.
- Remove "Only terminal tools exposed in this launch..." language for deferral.
- Keep the same lifecycle distinction: no deferred goal means the plan covers all current-iteration goal items and leaves no remaining items; a nonblank deferred goal carries concrete remaining current-iteration goal items into the next iteration.

### Executor Profile

File:

- `backend/src/agents/profile/main/executor.md`

Changes:

- Replace terminals:

```yaml
terminals:
  - submit_workflow_handoff
  - submit_generator_outcome
```

- Remove:

```yaml
terminal_routing: executor_routing.py
```

- Add nested handoff reminder while preserving the after-edit reminder:

```yaml
notification_triggers:
  - nested_workflow_handoff_disabled
  - request_workflow_after_edit
```

- Replace launch-filtering prose with prehook-enforced policy prose.
- Replace success/failure terminal prose with `submit_generator_outcome(status, outcome)`.

### Reducer Profile

File:

- `backend/src/agents/profile/main/reducer.md`

Changes:

- Replace terminals:

```yaml
terminals:
  - submit_reducer_outcome
```

- Replace success/failure terminal prose with `submit_reducer_outcome(status, outcome)`.

### Agent Definition Loading

Files:

- `backend/src/agents/definition/model.py`
- `backend/src/agents/definition/loader.py`

Changes:

- Remove the `terminal_routing` frontmatter field if no non-main profile still uses it.
- Remove routing module import and validation.
- Remove `AgentDefinition.terminal_router`.
- Update tests and docs that assert planner/executor are the only routed profiles.

### Agent Launch Composer

File:

- `backend/src/task_center/agent_launch/composer.py`

Changes:

- Stop depending on `TerminalToolRouter`.
- Load the agent definition directly.
- Require `context_recipe`.
- Return the unchanged `AgentDefinition`.

If a helper improves readability, add a small resolver with a neutral name such as `AgentLaunchDefinitionResolver`; do not keep the `TerminalToolRouter` name.

### Tool Registry And Names

Files:

- `backend/src/tools/_names.py`
- `backend/src/tools/_terminals/registry.py`
- `backend/src/tools/submission/_factory.py`
- `backend/src/tools/submission/planner/__init__.py`
- `backend/src/tools/submission/generator/__init__.py`
- `backend/src/tools/submission/reducer/__init__.py`

Changes:

- Add `SUBMIT_PLANNER_OUTCOME_TOOL_NAME = "submit_planner_outcome"`.
- Add `SUBMIT_GENERATOR_OUTCOME_TOOL_NAME = "submit_generator_outcome"`.
- Add `SUBMIT_REDUCER_OUTCOME_TOOL_NAME = "submit_reducer_outcome"`.
- Remove `SUBMIT_PLAN_CLOSES_GOAL_TOOL_NAME` and `SUBMIT_PLAN_DEFERS_GOAL_TOOL_NAME` after references are migrated.
- Remove split generator and reducer terminal constants after references are migrated.
- Replace the two planner terminal descriptors with one `submit_planner_outcome` descriptor.
- Replace split generator descriptors with one `submit_generator_outcome` descriptor.
- Replace split reducer descriptors with one `submit_reducer_outcome` descriptor.
- Register `submit_planner_outcome` in `make_submission_tools()`.
- Register `submit_generator_outcome` and `submit_reducer_outcome` in `make_submission_tools()`.
- Stop registering old split terminals once compatibility is intentionally removed.

### Prompt And Task Guidance

Create new `prompt.py` modules for the new canonical tools:

- `backend/src/tools/submission/planner/submit_planner_outcome/prompt.py`
- `backend/src/tools/submission/generator/submit_generator_outcome/prompt.py`
- `backend/src/tools/submission/reducer/submit_reducer_outcome/prompt.py`

Do not add a new TaskCenter task-guidance subsystem. Update existing guidance sources:

- `backend/src/tools/_terminals/registry.py` for terminal-selection guidance rendered into launch rows.
- `backend/src/agents/profile/main/planner.md` for planner role behavior.
- `backend/src/agents/profile/main/executor.md` for generator role behavior.
- `backend/src/agents/profile/main/reducer.md` for reducer role behavior.
- Existing `_prompt_guidance.py` files as needed so tool descriptions stay concise and consistent.

### Existing Handoff Tool

File:

- `backend/src/tools/submission/generator/submit_workflow_handoff/submit_workflow_handoff.py`

Changes:

- Add `DisallowNestedWorkflowHandoff("submit_workflow_handoff")` to prehooks.
- Keep the body as the terminal adapter over `GeneratorSubmissionContext.start_delegated_workflow(...)`.

### Background Task Gate

File:

- `backend/src/tools/_hooks/require_no_inflight_background_tasks.py`

Current issue:

- `submit_plan_defers_goal`, `submit_generator_failure`, and `submit_reduction_failure` are currently daemon-error bailout tools.
- After unification, tool name alone cannot distinguish close-plan from defer-plan or success from failure.

Target behavior:

- Confirmed in-flight background tasks still block every terminal.
- On daemon-count RPC failure:
  - `submit_planner_outcome` with nonblank `deferred_goal_for_next_iteration` may fail open like old `submit_plan_defers_goal`.
  - `submit_planner_outcome` without a deferred goal should follow the normal fail-safe behavior.
  - `submit_generator_outcome(status="failed", ...)` may fail open like old `submit_generator_failure`.
  - `submit_reducer_outcome(status="failed", ...)` may fail open like old `submit_reduction_failure`.
  - `submit_generator_outcome(status="success", ...)`, `submit_reducer_outcome(status="success", ...)`, and `submit_workflow_handoff` should follow normal fail-safe behavior.

### Architecture Docs

Refresh at least:

- `docs/architecture/task_center/agent-roles.html`
- `docs/architecture/task_center/terminal-tools.html`
- `docs/architecture/task_center/lifecycle.html`
- `docs/architecture/tools/terminals.html`
- `docs/architecture/tools/submission.html`
- `docs/architecture/agent_loops/prompt-context.html`

Remove claims that:

- `TerminalToolRouter` filters terminals at launch.
- Nested planners see only `submit_plan_closes_goal`.
- Nested executors lose `submit_workflow_handoff`.
- Generator and reducer success/failure are separate terminal tools.

Replace with:

- Stable terminal surfaces.
- Hard nested policy enforced by prehooks.
- Status/payload fields select submission mode.
- One-shot notifications explain disabled nested behavior before the first model turn.

## Files To Delete

Delete after references and tests are migrated:

```text
backend/src/task_center/_core/terminal_routing.py
backend/src/agents/profile/main/planner_routing.py
backend/src/agents/profile/main/executor_routing.py
backend/src/tools/submission/planner/submit_plan_closes_goal/
backend/src/tools/submission/planner/submit_plan_defers_goal/
backend/src/tools/submission/generator/submit_generator_success/
backend/src/tools/submission/generator/submit_generator_failure/
backend/src/tools/submission/reducer/submit_reduction_success/
backend/src/tools/submission/reducer/submit_reduction_failure/
```

Delete or rewrite tests that only prove launch-time terminal filtering:

```text
backend/tests/unit_test/test_task_center/test_agent_launch/test_terminal_tool_router.py
backend/tests/unit_test/test_agents/test_profile_routing.py
```

## Test Migration

### Planner Submission Tests

Update:

- `backend/tests/unit_test/test_tools/test_submission_planner_tools.py`

Coverage:

- `submit_planner_outcome` without deferred goal applies `planner_completes`.
- `submit_planner_outcome` with nonblank deferred goal applies `planner_defers`.
- deferred goal describes concrete current-iteration goal items deferred to the next iteration.
- blank deferred goal is rejected.
- nested planner deferral prehook rejects before state mutation.
- advisor approval still gates `submit_planner_outcome`.

### Generator And Reducer Submission Tests

Update:

- `backend/tests/unit_test/test_tools/test_submission_main_role_terminals.py`

Coverage:

- `submit_generator_outcome(status="success", outcome=...)` applies generator success.
- `submit_generator_outcome(status="failed", outcome=...)` applies generator failure.
- invalid generator status is rejected by pydantic before state mutation.
- `submit_reducer_outcome(status="success", outcome=...)` applies reducer success.
- `submit_reducer_outcome(status="failed", outcome=...)` applies reducer failure.
- invalid reducer status is rejected by pydantic before state mutation.
- advisor approval gates `submit_generator_outcome` and `submit_reducer_outcome`.

### Handoff Tests

Update or add:

- `backend/tests/unit_test/test_tools/test_submission_main_role_terminals.py`
- new focused hook tests under `backend/tests/unit_test/test_tools/test_hooks/`

Coverage:

- top-level `submit_workflow_handoff` still starts a child workflow.
- nested `submit_workflow_handoff` fails in the prehook and does not mutate the parent generator.
- advisor approval remains required for allowed handoff submissions.

### Notification Tests

Update:

- `backend/tests/unit_test/test_tools/test_submission_soft_reminders.py`
- `backend/tests/unit_test/test_agents/test_agent_markdown.py`

Coverage:

- planner nested deferral reminder fires once.
- generator nested handoff reminder fires once.
- existing `request_workflow_after_edit` still fires after edits.
- profile frontmatter declares the intended notification trigger list.

### Agent Definition Tests

Update:

- `backend/tests/unit_test/test_agents/test_agent_markdown.py`
- `backend/tests/unit_test/test_agents/test_registry_validation.py`
- `backend/tests/unit_test/test_agents/test_routing_acceptance.py`
- `backend/tests/unit_test/test_agents/test_skill_message.py`
- `backend/tests/unit_test/test_agents/test_skill_resolver.py`

Expected changes:

- No `terminal_routing` field.
- No `terminal_router` on definitions.
- Planner terminal list is `["submit_planner_outcome"]`.
- Executor terminal list is `["submit_workflow_handoff", "submit_generator_outcome"]`.
- Reducer terminal list is `["submit_reducer_outcome"]`.
- Terminal catalog renders from stable profile terminals.

### Mock Runner And Scenario Tests

Migrate old planner terminal calls in:

- `backend/src/task_center_runner/scenarios/**`
- `backend/src/task_center_runner/tests/mock/**`
- `backend/tests/unit_test/test_benchmarks/test_sweevo_mock_agent_execution.py`

Mapping:

```text
submit_plan_closes_goal(args) -> submit_planner_outcome(args)
submit_plan_defers_goal(args + deferred_goal_for_next_iteration) -> submit_planner_outcome(args + deferred_goal_for_next_iteration)
submit_generator_success(outcome, artifacts) -> submit_generator_outcome(status="success", outcome="... include artifact refs ...")
submit_generator_failure(outcome) -> submit_generator_outcome(status="failed", outcome=...)
submit_reduction_success(outcome) -> submit_reducer_outcome(status="success", outcome=...)
submit_reduction_failure(outcome) -> submit_reducer_outcome(status="failed", outcome=...)
```

Remove logic that checks `active_terminals` for absence of `submit_plan_defers_goal`; replace it with scenario behavior that either:

- expects `submit_planner_outcome` with a deferred goal to fail in nested context, or
- emits a close-plan payload for nested workflow scenarios.

## Suggested Migration Order

1. Add `workflow_depth.py` and tests for depth calculation.
2. Add `submit_planner_outcome` alongside old planner terminals.
3. Add planner and generator prehooks.
4. Add notification triggers and wire profile frontmatter.
5. Migrate tests and mock scenarios from old split terminal names to `submit_planner_outcome`, `submit_generator_outcome`, and `submit_reducer_outcome`.
6. Remove `terminal_routing` support from agent definition loading and composer.
7. Delete `TerminalToolRouter`, `planner_routing.py`, and `executor_routing.py`.
8. Delete old split terminal packages.
9. Refresh architecture docs and search index.
10. Run focused verification.

## Verification Commands

Start with focused unit slices:

```bash
uv run pytest \
  backend/tests/unit_test/test_tools/test_submission_planner_tools.py \
  backend/tests/unit_test/test_tools/test_submission_main_role_terminals.py \
  backend/tests/unit_test/test_tools/test_submission_soft_reminders.py \
  backend/tests/unit_test/test_tools/test_hooks \
  backend/tests/unit_test/test_agents/test_agent_markdown.py \
  backend/tests/unit_test/test_agents/test_skill_message.py
```

Then run routing-removal and mock-runner slices:

```bash
uv run pytest \
  backend/tests/unit_test/test_agents \
  backend/src/task_center_runner/tests/mock/task_center \
  backend/src/task_center_runner/tests/mock/contracts
```

Finally run lint/type checks relevant to touched packages:

```bash
uv run ruff check backend/src/tools backend/src/agents backend/src/task_center backend/tests/unit_test/test_tools backend/tests/unit_test/test_agents
uv run pyright backend/src/tools backend/src/agents backend/src/task_center
```

## Resolved Decisions

1. Compatibility aliases for `submit_plan_closes_goal` and `submit_plan_defers_goal`.
   - Decision: no aliases; migrate callers to `submit_planner_outcome`.

2. Compatibility aliases for split generator/reducer terminals.
   - Decision: no aliases; migrate callers to `submit_generator_outcome(status, outcome)` and `submit_reducer_outcome(status, outcome)`.

3. Generator artifacts field.
   - Decision: no separate field; put artifact references in `outcome` so downstream context has one reducer-visible source.

4. Resolver extraction.
   - Decision: no resolver; `AgentEntryComposer` uses the registered profile directly now that launch-time terminal routing is removed.

5. Notification wording.
   - Decision: launch reminders say "disabled in nested workflows"; prehook failures start with "BLOCKED".

## Expected End State

There is no launch-time terminal routing. Main agent terminal surfaces are stable. Lifecycle constraints are enforced by prehooks at the tool boundary, and agents receive one-shot system reminders when their current workflow depth disables a payload mode or handoff path.
