# SWE-EVO complex scenario testing - Phase 2 implementation plan

**Date:** 2026-05-08
**Scope:** Implement the deferred complex scenario layer on top of
`backend/src/benchmarks/sweevo/live_test/`, using the real TaskCenter runtime,
the real Daytona sandbox, and deterministic mock agents. The scenario must use
the exact user input produced by the existing code path, without augmenting or
reconstructing it from the PR-description CSV inside the scenario, and must
exercise dynamic DAG planning, midpoint verifier gates, retry attempts,
partial-plan continuation, and recursive missions for oversized subtasks.

---

## 1. Locked test environment

Phase 2 uses the Dask release-bundle instance unless explicitly overridden by
`EOS_SWEEVO_INSTANCE`.

```python
def sweevo_instance():
    return select_sweevo_instance(
        instance_id=os.getenv("EOS_SWEEVO_INSTANCE", "dask__dask_2023.3.2_2023.4.0")
    )
```

Implementation requirement:

- Keep this as the fixture contract in `backend/src/benchmarks/sweevo/live_test/fixtures.py`.
- The default full-case run must therefore use
  `dask__dask_2023.3.2_2023.4.0`.
- Do not replace, rewrite, or augment the user input. The scenario should
  inspect the same `prompt_text` that `run_scenario(...)` passes to
  `start_task_center_entry_run(...)`: either the explicit `user_prompt`
  argument or the current `build_sweevo_user_prompt(...)` output.
- The scenario must not do a second CSV lookup to construct a richer task
  prompt. If the existing prompt contains a `<pr_description>` block, it may be
  inspected as part of the already-rendered user input.
- Test assertions should report the resolved `instance_id` in `run.json` and in
  the returned `RunReport`.

Current user-input source of truth:

```text
run_scenario(..., user_prompt=None)
  -> prompt_text = user_prompt if provided
  -> otherwise build_sweevo_user_prompt(instance, repo_dir=repo_dir)
  -> start_task_center_entry_run(prompt=prompt_text, ...)
```

The complex scenario should observe `prompt_text` after this point. It should
not create a second, scenario-specific prompt.

---

## 2. Current implementation baseline

Phase 1 already provides the load-bearing framework:

- `run_scenario(...)` starts the real TaskCenter entry run, wires the audit bus,
  registers scenario hooks, and records ORM snapshots.
- `CorrectnessTesting` proves retry, partial planning, continuation episode, and
  final evaluator success in one static scenario.
- `Scenario` currently exposes `planner_response`, `executor_actions`,
  `evaluator_response`, and `hooks`.
- `MockSquadRunner` currently understands `entry_executor`, `planner`,
  `executor`, and `evaluator`.
- Verifier submission tools already exist, but the SWE-EVO mock squad does not
  register or dispatch a verifier profile yet.
- Squash/layer-count events are declared but have no producer because public
  sandbox results do not expose layer depth or squash trigger metadata.

Phase 2 should extend this framework rather than replacing it.

---

## 3. Scenario goal

Create a new scenario:

```text
full_case_user_input
```

It should simulate a real complex agent workflow from beginning to end:

1. Entry executor receives the exact user input already produced by the code.
2. Planner decomposes the requirements found in that user input into dynamic
   work packages.
3. Executors run package-level probes and edits in parallel waves where possible.
4. Verifiers guard checkpoints after executor waves, not one verifier per
   executor.
5. A verifier failure creates an attempt retry with failed-attempt context.
6. A successful partial plan creates the next episode through `continuation_goal`.
7. An oversized package delegates a recursive mission through
   `request_mission_solution`.
8. The parent mission waits for the recursive mission close report before its
   own guard can pass.
9. The final episode uses a full plan and closes through the final evaluator.

The scenario should assert structural invariants, not exact task counts.

---

## 4. Dynamic user-input inspection

Add a small parser module:

```text
backend/src/benchmarks/sweevo/live_test/scenarios/user_input.py
```

Suggested DTOs:

```python
@dataclass(frozen=True, slots=True)
class RequirementItem:
    id: str
    heading: str
    text: str
    pr_id: str | None
    subsystem: str
    risk: str
    weight: int

@dataclass(frozen=True, slots=True)
class WorkPackage:
    id: str
    title: str
    subsystem: str
    item_ids: tuple[str, ...]
    weight: int
    risk: str
    deps: tuple[str, ...] = ()
    recursive_candidate: bool = False
```

Parsing rules:

- Take the already-rendered user prompt string as input. Do not call
  `load_pr_description_overrides(...)`, `pr_description_for_instance(...)`, or
  `csv.DictReader` from the scenario parser.
- If the prompt contains `<pr_description>...</pr_description>`, inspect that
  block as existing user input. If it does not, inspect the full user prompt.
- Extract bullet-shaped requirements from the inspected user-input text.
- Preserve section headings when available, such as `Enhancements`,
  `Bug Fixes`, `Deprecations`, `Documentation`, and `Maintenance`.
- Assign rough subsystem labels from keyword rules:
  `config`, `io`, `dataframe`, `array`, `distributed`, `cli`, `parquet`,
  `compat`, `docs`, `maintenance`, `unknown`.
- Assign risk:
  - `high`: cross-subsystem, deprecation, IO/parquet, compatibility, runtime.
  - `medium`: user-visible behavior, CLI, config.
  - `low`: docs, narrow maintenance, dependency pins.
- Compute `weight` from risk + item size. This is only a planning heuristic,
  not a grading metric.

---

## 5. Dynamic DAG generation policy

Do not hardcode executor/verifier counts. Generate a DAG from packages.

Executor package rules:

- Target package weight: `10-12`.
- High-risk package cap: `6` bullet items.
- Keep one package within one dominant subsystem when possible.
- Split cross-subsystem packages before execution.
- Mark a package as `recursive_candidate` when:
  - `weight > 30`, or
  - it spans more than `3` subsystems, or
  - it mixes config + IO + runtime + compatibility semantics.

Verifier guard rules:

- A verifier guards a checkpoint, not an individual executor.
- Insert a verifier after each executor wave.
- Insert a verifier before any dependent integration wave.
- Insert a verifier after a recursive mission returns.
- Insert a final pre-evaluator verifier in the closing episode.
- A verifier may depend on multiple executor task ids.

Expected shape for large user-input cases:

```text
Wave 1:
  E_config  E_io  E_dataframe  E_cli  E_compat  E_docs  E_maintenance_a
      \       |       |          |       |        |          /
                         V_wave_1

Wave 2:
  E_integration_a  E_integration_b  E_runtime_followup
          \             |              /
                         V_wave_2

Recursive branch:
  E_delegate_oversized_package -> request_mission_solution(...)
                         |
                    V_recursive_return
```

For the default Dask user input produced by the current fixture, the
implementation should normally produce a large but bounded graph, roughly:

```text
executors: 20-40
verifiers: 6-14
recursive missions: 1-3
```

Tests should not assert those exact numbers. They should assert lower bounds and
relationship invariants.

---

## 6. Scenario lifecycle

### Episode 1 - decomposition retry

Attempt 1:

- Planner submits a full plan for user-input inventory.
- Executor parses and clusters requirements from the already-rendered user
  input.
- Verifier intentionally fails because the first ledger omits at least one
  high-risk category or recursive candidate.
- Evaluator failure closes the attempt as failed.

Attempt 2:

- Planner receives failed-attempt context.
- Executor produces corrected requirement ledger and package DAG.
- Verifier passes coverage.
- Evaluator succeeds.
- Planner result is a partial plan with a `continuation_goal`, so Episode 2 is
  created.

Important invariant:

```text
Episode 2 must be created only after a successful partial plan.
A successful full plan must not create Episode 2.
```

### Episode 2 - implementation DAG with recursive mission

Attempt 1:

- Planner submits a partial plan generated from the requirement ledger.
- Executor tasks run in dynamic DAG waves.
- Verifier guards wave checkpoints.
- One executor delegates an oversized package with `request_mission_solution`.
- The recursive mission performs its own planning, execution, verification, and
  evaluator close.
- Parent verifier fails once if the recursive close report or a required
  checkpoint is missing, forcing an Episode 2 retry.

Attempt 2:

- Planner sees failed-attempt context.
- Only failed or unverified work is replayed.
- Wave verifier passes.
- Recursive-return verifier passes.
- Evaluator succeeds.
- If remaining packages exist, submit a partial plan to Episode 3. Otherwise,
  use a full plan and close.

### Recursive mission

The recursive mission should use the same policy, but with a smaller budget:

```text
sub episode 1: decompose oversized package
sub episode 2: execute package DAG waves
sub episode 3: reconcile and close
```

The parent executor that requested the mission should remain blocked through
the existing TaskCenter `WAITING_COMPLEX_TASK` flow until the mission close
report is delivered.

### Final episode - reconciliation

- Planner submits a full plan.
- Executor builds final coverage ledger and artifact/readback summary.
- Executor runs final test-command simulation or targeted readback probes.
- Verifier checks every high-risk requirement category has evidence.
- Evaluator closes the root mission.

---

## 7. Required code changes

### 7.1 Register verifier in the mock squad

Update:

```text
backend/src/benchmarks/sweevo/live_test/squad/definitions.py
```

Add:

```python
AgentDefinition(
    name="verifier",
    description="SWE-EVO mock verifier",
    role="verifier",
    context_recipe="generator_v1",
    allowed_tools=["read_file", "shell"],
    terminals=["submit_verification_success", "submit_verification_failure"],
)
```

Reason:

- Planner submissions already allow generator-capable agents named `verifier`.
- The verifier terminal tools require the launched agent profile role to be
  `verifier`.

### 7.2 Extend scenario protocol without breaking existing scenarios

Update:

```text
backend/src/benchmarks/sweevo/live_test/scenarios/base.py
```

Add optional methods with defaults:

```python
def verifier_response(self, ctx: ScenarioContext) -> ToolCallSpec: ...
def recursive_mission_goal(self, ctx: ScenarioContext) -> str | None: ...
```

Keep `executor_actions(...)` for compatibility with `CorrectnessTesting`.

Also extend `ScenarioContext` with:

- `task_id`
- `agent_name`
- `task_input`
- `graph_summary`
- `requirement_ledger`
- `package_plan`

These can start as optional fields so Phase 1 remains stable.

### 7.3 Extend mock runner dispatch

Update:

```text
backend/src/benchmarks/sweevo/live_test/squad/runner.py
```

Add:

- `_run_verifier(...)`
- verifier invocation/success/failure events
- action support for `request_recursive_mission`
- package action support, for example:
  - `inspect_user_input`
  - `execute_package:<package_id>`
  - `verify_wave:<wave_id>`
  - `verify_recursive_return:<mission_id>`
  - `final_reconciliation`

Verifier terminal mapping:

```python
submit_verification_success -> VERIFIER_SUCCESS
submit_verification_failure -> VERIFIER_FAILURE
```

Do not treat verifier success as evaluator success. Verifiers are generator
tasks inside an attempt; evaluators remain the final attempt judge.

### 7.4 Add verifier events

Update:

```text
backend/src/benchmarks/sweevo/live_test/audit/events.py
```

Add:

```python
VERIFIER_INVOKED = "verifier_invoked"
VERIFIER_SUCCESS = "verifier_success"
VERIFIER_FAILURE = "verifier_failure"
RECURSIVE_MISSION_REQUESTED = "recursive_mission_requested"
RECURSIVE_MISSION_COMPLETED = "recursive_mission_completed"
```

Keep `MISSION_REQUESTED` as the lifecycle-level event. The recursive-specific
events are scenario assertions, not new TaskCenter lifecycle policy.

### 7.5 Make audit directories role-readable

Current task dirs use persisted harness role, so verifier generator tasks can
look like `NN_generator_<task_id>`. For scenario browsing, prefer the agent
profile when present:

```text
NN_executor_<task_id>
NN_verifier_<task_id>
```

Update only the SWE-EVO audit path resolver. Do not change TaskCenter persisted
role semantics; verifier and executor are both generator tasks at the lifecycle
level.

### 7.6 Implement hook injection

Update:

```text
backend/src/benchmarks/sweevo/live_test/hooks/registry.py
backend/src/benchmarks/sweevo/live_test/hooks/builtins.py
```

Implement:

- `MutableMockState.inject_failure(role=..., attempt_id=...)`
- `MutableMockState.replace_next_planner_response(spec)`
- `fail_verifier_at(checkpoint=...)`
- `assert_guard_after_wave(wave_id=...)`
- `assert_recursive_mission_closed_before_parent_guard()`

The runner should consult `mutable_state` before selecting deterministic
scenario responses.

### 7.7 Scenario registry and test file

Add:

```text
backend/src/benchmarks/sweevo/live_test/scenarios/full_case_user_input.py
backend/src/benchmarks/sweevo/live_test/tests/test_full_case_user_input.py
```

Update:

```text
backend/src/benchmarks/sweevo/live_test/scenarios/__init__.py
```

Register:

```python
"full_case_user_input": FullCaseUserInput
```

The CLI path then works through the existing:

```bash
uv run python -m benchmarks.sweevo --scenario full_case_user_input
```

---

## 8. Test assertions

The live test should assert:

- The resolved `sweevo_instance.instance_id` defaults to
  `dask__dask_2023.3.2_2023.4.0` when `EOS_SWEEVO_INSTANCE` is unset.
- The entry prompt is exactly the prompt produced by the current code path:
  either the `user_prompt` passed to `run_scenario(...)` or the fallback
  `build_sweevo_user_prompt(...)` output. If it contains a `<pr_description>`
  block, that block is inspected only because it is already present in the
  user input.
- The requirement ledger contains more than `100` bullet-shaped items for the
  default Dask case.
- At least one planner submission is partial and creates a continuation episode.
- No continuation episode is created by a successful full plan.
- At least one verifier depends on multiple executor tasks.
- At least one verifier failure causes an attempt retry.
- The retry planner context includes failed-attempt evidence.
- At least one executor calls `request_mission_solution`.
- The root mission has at least one nested mission.
- A parent verifier depending on recursive output runs only after the recursive
  mission close report is delivered.
- The final evaluator runs after the final verifier passes.
- The audit tree contains mission, episode, attempt, executor, verifier, and
  evaluator task directories.

Do not assert exact executor/verifier counts. Assert dynamic ranges:

```text
executor_count >= 12
verifier_count >= 4
verifier_count < executor_count
recursive_mission_count >= 1
```

The exact counts can vary with future fixture prompt changes or parser
refinements.

---

## 9. Verification commands

Focused unit-level checks first:

```bash
uv run pytest backend/tests/unit_test/test_benchmarks/test_sweevo_audit_recorder.py -q
uv run pytest backend/tests/unit_test/test_task_center/test_lifecycle/test_attempt_orchestrator.py -q
uv run pytest backend/tests/unit_test/test_task_center/test_lifecycle/test_phase04_mission_request_start.py -q
```

Scenario live check:

```bash
EOS_SWEEVO_INSTANCE=dask__dask_2023.3.2_2023.4.0 \
uv run pytest backend/src/benchmarks/sweevo/live_test/tests/test_full_case_user_input.py -q
```

Tier gate:

```bash
EOS_SWEEVO_INSTANCE=dask__dask_2023.3.2_2023.4.0 \
uv run python -m backend.tests.live_e2e_test._tools.run_tiered --tier 7
```

---

## 10. Acceptance criteria

Phase 2 is complete when:

1. `full_case_user_input` is registered and runnable through pytest and the
   SWE-EVO CLI scenario entry.
2. The fixture default is the Dask instance shown in section 1.
3. The scenario builds its work DAG from the exact user input passed to
   TaskCenter, not from hardcoded executor/verifier counts and not from a second
   CSV parse.
4. Verifiers are checkpoint guards over executor waves.
5. At least one recursive mission is requested by an executor and closed before
   the parent guard passes.
6. At least one verifier failure causes an attempt retry, and the retry planner
   sees failed-attempt context.
7. At least one successful partial plan creates a continuation episode.
8. The final episode uses a full plan and closes the root mission through the
   evaluator.
9. The audit output is browseable and distinguishes executor and verifier task
   directories.
10. Tier 7 passes against a real Daytona sandbox.
