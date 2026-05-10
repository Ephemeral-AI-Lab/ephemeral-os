---
title: "Live E2E Scenario Suite — Design"
tags: ["live-e2e", "scenarios", "scenario-suite", "test-design", "load-bearing"]
created: 2026-05-10T22:00:00.000Z
updated: 2026-05-10T22:00:00.000Z
sources: ["live-e2e-testing-framework-design.md", "live-e2e-scenarios-full-migration.md"]
links: ["live-e2e-testing-framework-design.md", "task-center-pipeline.md", "sandbox-subsystem.md", "tools-hooks-guardrails-agents-notifications-messages.md", "context-engine-recipes.md"]
category: decision
confidence: high
schemaVersion: 1
---

# Live E2E Scenario Suite — Design

_Drafted 2026-05-10. Specifies the full scenario taxonomy under_ `backend/src/live_e2e/scenarios/` _that the harness from_ `live-e2e-testing-framework-design.md` _drives. Existing scenarios (`correctness_testing`, `full_case_user_input`, `full_stack_adversarial`) are **composite** end-to-end runs. This document fills out the **focused** scenarios that exercise one concern at a time so failures pinpoint the responsible subsystem._

## TL;DR

Existing live-e2e coverage = three composite scenarios that exercise everything at once. When one fails, root-cause is expensive to find. **This document specifies a folder-per-concern taxonomy of focused scenarios** so each subsystem (task_center pipeline, sandbox, tools/guardrails/notifications, context engine, planner validation) gets dedicated coverage. Composite scenarios stay as end-to-end smoke tests on top.

The harness (the `runner=` seam in `start_task_center_entry_run`) and the scenario protocol (4 decision methods + `hooks()`) **do not change**. Every new scenario plugs into the existing `MockSquadRunner` and writes audit artifacts under the canonical `scenario_logs/<name>/<UTCstamp>_<run_id>/` layout.

## Folder structure

```
backend/src/live_e2e/scenarios/
  __init__.py                          # SCENARIO_REGISTRY
  base.py                              # Scenario protocol + ScenarioBase + ScenarioContext + ToolCallSpec
  user_input.py                        # build_user_input_plan helper (composite scenarios)
  README.md                            # pointer to this wiki page

  # Composite scenarios (existing, full end-to-end coverage)
  correctness_testing.py               # entry → mission → 2 episodes → close
  full_case_user_input.py              # dynamic DAG from rendered user input
  full_stack_adversarial.py            # OCC + overlay + layerstack + LSP matrices

  # Shared scenario helpers (NEW)
  _utils/
    __init__.py
    plans.py                           # minimal_full_plan(), preflight_full_plan(), preflight_partial_plan()
    mission_helpers.py                 # is_root_mission(), is_recursive_mission()
    inspectors.py                      # field() task_input parser

  # Pipeline state-machine scenarios (NEW)
  pipeline/
    __init__.py
    initial_mission.py                 # ← REFERENCE: 1 mission, 1 episode, 1 attempt, success
    episodic_continuation.py           # ← REFERENCE: partial plan → continuation episode
    attempt_retry_evaluator_failure.py # ← REFERENCE: attempt 1 fails (evaluator), attempt 2 passes
    dependency_dag_serial.py           # ← REFERENCE: A → B → C; assert ready_pending order
    dependency_dag_mixed.py            # ← REFERENCE: a→(b,c)→d→(e,f)→g; mixed serial+parallel
    generator_failure_quiescence.py    # ← REFERENCE: gen fails, dispatcher waits for siblings, retry passes
    attempt_budget_exhausted.py        # ← REFERENCE: every attempt fails → mission failed
    attempt_retry_planner_failure.py   # planner submits failure, retry planner
    attempt_retry_generator_failure.py # generator submits failure, retry whole attempt
    nested_mission.py                  # request_mission_solution; child mission spawns + closes
    nested_mission_failure.py          # child mission fails, parent receives failure report
    dependency_dag_parallel.py         # A,B,C → D (fan-in)
    dependency_dag_diamond.py          # A → B,C → D
    dependency_blocked_descendants.py  # A fails → B,C marked BLOCKED

  # Sandbox subsystem scenarios (NEW)
  sandbox/
    __init__.py
    occ_concurrent_conflicts.py        # ← REFERENCE: write→edit conflict via sandbox_integrity action
    setup_and_daemon.py                # bootstrap_daytona + ensure_workspace_base + readiness probe
    occ_basic_writes.py                # write_file → read_file round trip; assert layer published
    occ_serial_merger.py               # disjoint edits to same file → both succeed
    occ_stale_conflict.py              # edit_file after shell mutated same path
    overlay_basic_run.py               # shell echoing to file → overlay capture → OCC commit
    overlay_capture_changes.py         # multiple file mutations in one shell → all captured
    overlay_symlink_handling.py        # symlink inside / symlink escape attempts
    layerstack_publish.py              # single layer publish; assert manifest grew
    layerstack_lease_protection.py     # lease prevents read of in-flight layer
    layerstack_squash.py               # cross 32-layer threshold → SquashWorker fires
    layerstack_workspace_base.py       # build_workspace_base content addressing
    command_exec_routing.py            # guarded shell paths through occ.routing
    lsp_plugin_install.py              # ensure_installed idempotency + plugin.ensure
    lsp_diagnostics_refresh.py         # diagnostics before/after edit
    lsp_hover_signature.py             # hover returns up-to-date signature post-edit
    lsp_cross_file_references.py       # find_references after rename
    lsp_after_edit_refresh.py          # workspace symbols include freshly written file

  # Tool execution + guardrail + notification scenarios (NEW)
  tools/
    __init__.py
    sandbox_toolkit_round_trip.py      # write→read→edit→shell exercise
    batch_edit_atomicity.py            # multi-edit batch; conflict mid-batch
    request_mission_before_edit_gate.py# RequestMissionBeforeEditGate fail path
    resolver_success_limit_gate.py     # ResolverSuccessLimitGate at limit
    helper_request_gate.py             # HelperRequestGate caller-role check
    helper_role_gate.py                # HelperRoleGate role+agent_type validation
    harness_role_gate.py               # HarnessRoleGate task-row role check
    harness_agent_profile_gate.py      # HarnessAgentProfileGate role match
    terminal_tool_exclusivity.py       # terminal + sibling read → validate_tool_batch reject
    max_step_limit.py                  # tool_call_limit reached → RESOURCE_LIMIT exit
    notification_resolver_limit.py     # 4+ unresolved resolver calls → reminder fires once
    notification_request_mission_after_edit.py
    notification_budget_warning.py     # at 50%/75%/90% thresholds
    pre_post_hook_lifecycle.py         # pre-hook replaces input; post-hook replaces result

  # Context engine recipe scenarios (NEW)
  context/
    __init__.py
    planner_initial_mission.py         # ep1, attempt1 → single episode_goal block
    planner_attempt_retry.py           # failed_attempt_landscape with fail_reason
    planner_attempt_retry_overflow.py  # >6 fails → MEDIUM summary block
    planner_episodic_continuation.py   # ep2+ → mission_goal + prior-episode pairs
    generator_no_dependencies.py       # no `# Dependency Results` heading
    generator_with_dependencies.py     # dependency_summary blocks present
    generator_re_planned.py            # new attempt → new task_specification text
    evaluator_initial.py               # task_specification REQUIRED + evaluation_criteria
    evaluator_with_failed_attempts.py  # evaluator does NOT include failed_attempt_landscape
    evaluator_episodic_continuation.py # ep2+ evaluator gets prior-episode pairs
    helper_advisor_inheritance.py      # parent blocks demoted, "# Parent context" heading
    helper_resolver_inheritance.py
    entry_executor_minimal.py          # only entry_request block, no mission/episode

  # Planner validation rejection scenarios (NEW)
  planner_validation/
    __init__.py
    duplicate_local_id.py              # ← REFERENCE: planner emits duplicate task ids → planner_failed
    unknown_dep.py                     # task deps reference unknown id
    cycle_in_deps.py                   # A→B, B→A
    partial_without_continuation_goal.py
    unknown_agent_name.py              # plan references unregistered agent
    empty_tasks.py                     # plan with zero tasks
```

## Naming + protocol conventions

**File name = scenario name**, snake_case. The scenario class name is the PascalCase of the file. Both `name` field and registry key match the file name exactly. This makes greppability one-shot.

```python
# pipeline/initial_mission.py
class InitialMission(ScenarioBase):
    name = "pipeline.initial_mission"     # dotted form: <package>.<file>
    expected_event_sequence = (...)
    def planner_response(self, ctx): ...
    def evaluator_response(self, ctx): ...
```

The dotted `name` form (`pipeline.initial_mission`) is what flows into the audit `run_dir`. Existing composite scenarios keep their bare names (`correctness_testing`, `full_case_user_input`, `full_stack_adversarial`) — they are at the top level for historical reasons.

### Scenario base contract — every scenario implements

| Method | Required | Notes |
|---|---|---|
| `name: str` | yes | `<package>.<file>` for new; bare for composites |
| `expected_event_sequence: tuple[EventType, ...]` | yes | Asserted in tests via `tuple(report.seen_event_types) == scenario.expected_event_sequence`. Tight assertion catches event-ordering regressions. |
| `planner_response(ctx) -> ToolCallSpec` | yes | Returns one of `submit_full_plan` / `submit_partial_plan` per `(episode.sequence_no, attempt.attempt_sequence_no)` branch. |
| `executor_actions(ctx) -> Sequence[str]` | optional | Default `()`. Action strings are matched in `MockSquadRunner._run_executor_actions` (`squad/runner.py:362-470`). Reuse existing actions when possible. |
| `verifier_response(ctx) -> ToolCallSpec` | optional | Required only when plan includes a `verifier` task. |
| `evaluator_response(ctx) -> ToolCallSpec` | yes | Returns one of `submit_evaluation_success` / `submit_evaluation_failure`. |
| `recursive_mission_goal(ctx) -> str \| None` | optional | Required only for `request_recursive_*` action. |
| `hooks() -> Sequence[Hook]` | optional | Default `()`. |

### Available executor action strings (`squad/runner.py`)

| Action | Tool calls | Use for |
|---|---|---|
| `preflight` | one `shell` | minimal "executor ran" evidence; cheapest exec |
| `fail` / `fail:<reason>` | `submit_execution_failure` | force a generator-stage terminal failure (quiescence + retry + budget exhaustion scenarios) |
| `sandbox_integrity` | write_file + read_file + edit_file + shell + batch edit + conflict | OCC / overlay / layerstack coverage |
| `final_probe` | read_file + shell | continuation-episode readback proof |
| `inspect_user_input` | read_file | user-input ingest scenarios |
| `inspect_full_user_input` | read_file + write_file | full-stack ingest scenarios |
| `execute_package:<id>` | varies (per package) | dynamic package scenarios |
| `final_reconciliation` | write_file | dynamic reconciliation scenarios |
| `recursive_step` | write_file | inside recursive mission |
| `request_recursive_mission:<id>` | request_mission_solution | nested mission scenarios |
| `request_recursive_matrix:<id>` | request_mission_solution | full-stack recursive |
| `recursive_oversized_matrix` | write_file + read_file | recursive package work |
| `full_stack_final_reconciliation` | write_file | full-stack close |
| `occ_conflict_matrix` / `overlay_edge_matrix` / `layerstack_squash_lease` / `lsp_refresh_semantics` | matrix-driven | full-stack adversarial |

**Reuse first.** Adding a new action requires editing `squad/runner.py` AND likely `squad/tool_scripts.py`. Keep the action set stable; reach for new actions only when no existing one exercises the right code path.

## What each subpackage proves

### `pipeline/` — task_center state machine

Scenarios here exercise the orchestrator/dispatcher/episode-manager/mission-handler control flow. They use the **lightest possible executor action** (`preflight`) and rely on planner/evaluator decisions to drive the state machine into specific configurations. Failures here mean a regression in `task_center/` proper.

Coverage matrix (one scenario per cell):
| State machine concern | Scenario |
|---|---|
| Initial mission, single attempt | `initial_mission` |
| Attempt retry on planner failure | `attempt_retry_planner_failure` |
| Attempt retry on generator failure | `attempt_retry_generator_failure` |
| Attempt retry on evaluator failure | `attempt_retry_evaluator_failure` |
| Attempt budget exhaustion | `attempt_budget_exhausted` |
| Episodic continuation via partial plan | `episodic_continuation` |
| Recursive (nested) mission close | `nested_mission` |
| Recursive mission failure propagation | `nested_mission_failure` |
| DAG topology — serial chain | `dependency_dag_serial` |
| DAG topology — fan-in | `dependency_dag_parallel` |
| DAG topology — diamond | `dependency_dag_diamond` |
| DAG failure → blocked descendants | `dependency_blocked_descendants` |

Assertions: `report.task_center_status`, `report.seen_event_types`, `report.graph_summary["missions"]` shape (mission count, episode count per mission, attempt count per episode), per-Attempt `attempt_sequence_no`, `fail_reason`.

### `sandbox/` — sandbox subsystem

Scenarios drive the sandbox subsystem (OCC, overlay, layerstack, command_exec, plugin/LSP, daemon) through tool calls. Most reuse the `sandbox_integrity` action which already exercises write/read/edit/shell/batch/conflict; finer-grained scenarios add focused new actions or rely on hand-crafted shell sequences.

Coverage maps to `sandbox-subsystem.md`:
| Subsystem | Scenarios |
|---|---|
| occ | `occ_basic_writes`, `occ_concurrent_conflicts`, `occ_serial_merger`, `occ_stale_conflict` |
| overlay | `overlay_basic_run`, `overlay_capture_changes`, `overlay_symlink_handling` |
| layer_stack | `layerstack_publish`, `layerstack_lease_protection`, `layerstack_squash`, `layerstack_workspace_base` |
| command_exec | `command_exec_routing` |
| plugin/LSP | `lsp_plugin_install`, `lsp_diagnostics_refresh`, `lsp_hover_signature`, `lsp_cross_file_references`, `lsp_after_edit_refresh` |
| daemon | `setup_and_daemon` |

Assertions: `report.sandbox_events.jsonl` event types, sandbox-derived `EventType.SANDBOX_*`, file content via `read_file` post-mortem in scenario verifier.

### `tools/` — tool execution, guardrails, notifications

Scenarios verify that:
1. Submission gate hooks reject the right calls (`HookResult.fail`).
2. Pre/post hook pipelines compose correctly.
3. `tool_call_limit` enforces `RESOURCE_LIMIT` exits.
4. Terminal-tool exclusivity is enforced by `validate_tool_batch`.
5. Notification rules fire at the right turn and dedupe via `notification_fired`.

Each guardrail scenario builds a plan that **provokes** the gate, then asserts the resulting `tool_results` carry the expected `hookSpecificOutput` payload.

### `context/` — context engine recipes

Scenarios build specific (mission/episode/attempt) configurations and capture the rendered `LaunchBundle` via the `prompt_inspector` (`squad/prompt_inspector.py`) to assert the rendered prompt structure (block count, headings, priority order) and `packet.blocks` shape. The model API is bypassed entirely — these are pure recipe-output assertions.

### `planner_validation/` — planner submission rejection

Scenarios where the planner emits an invalid `submit_full_plan` / `submit_partial_plan`. Assertion: attempt closes with `fail_reason="planner_failed"`, `TaskCenterInvariantViolation` raised, no generator/evaluator ran.

## Test files

Each scenario gets a paired pytest file under `backend/src/live_e2e/tests/<package>/test_<scenario>.py`.

```
backend/src/live_e2e/tests/
  conftest.py                          # pytest_plugins = ["live_e2e.fixtures"]
  test_runner_imports.py               # offline wiring tests (existing)
  test_stores.py                       # PG round-trip (existing)
  test_scenario_suite_imports.py       # NEW: registry membership + protocol conformance
  pipeline/
    test_initial_mission.py            # ← REFERENCE
    test_episodic_continuation.py      # ← REFERENCE
    test_attempt_retry_evaluator_failure.py  # ← REFERENCE
    test_dependency_dag_serial.py      # ← REFERENCE
    ...
  sandbox/
    test_occ_concurrent_conflicts.py   # ← REFERENCE
    ...
  tools/...
  context/...
  planner_validation/
    test_duplicate_local_id.py         # ← REFERENCE
    ...
  sweevo/
    test_correctness.py                # existing composite tests
    test_correctness_via_live_e2e.py
    test_full_case_user_input.py
    test_full_stack_adversarial.py
```

Pipeline/planner_validation/context tests do **not** require a real Daytona sandbox — they exercise the task_center state machine and recipe outputs only. Mark with `@pytest.mark.live_e2e_offline` so the live tier doesn't double-run them.

Sandbox/tools tests **do** require Daytona (real OCC, real overlay, real plugin runtime). Mark with `@pytest.mark.live_e2e_daytona` so they only run in the daytona-enabled tier.

## Sandbox provisioner — generic vs SWE-EVO

Composite scenarios (`correctness_testing` etc.) consume the SWE-EVO sandbox provisioner via `live_e2e.sweevo_adapter`. Most new focused scenarios do **not** need a SWE-EVO instance — they just need a sandbox with a writable workspace.

Add `live_e2e.generic_adapter` (TBD, separate PR):

```python
# live_e2e/generic_adapter.py
async def create_generic_test_sandbox() -> dict[str, Any]:
    """Provision a minimal sandbox with /workspace as the test repo dir."""
    ...

GENERIC_ENTRY_PROMPT = "Run the assigned scenario. Use the harness tools as needed."
```

Then a non-SWE-EVO scenario test reads:
```python
@pytest.mark.live_e2e_daytona
async def test_initial_mission(generic_sandbox, audit_dir, stores):
    report = await run_scenario(
        InitialMission(),
        sandbox_id=generic_sandbox["sandbox_id"],
        audit_dir=audit_dir,
        stores=stores,
        repo_dir="/workspace",
        entry_prompt=GENERIC_ENTRY_PROMPT,
    )
    assert report.task_center_status == "succeeded"
```

For the **scaffolding PR** (this initial buildout), pipeline/planner_validation reference scenarios will use the existing `sweevo_sandbox` fixture so they ship with passing tests; the generic-adapter PR is tracked as a follow-up.

## Reference scenarios shipped in scaffolding PR

Nine fully-implemented scenarios that other contributors can copy when implementing the rest. Coverage spans every category:

1. **`pipeline/initial_mission.py`** — `InitialMission` — single-attempt happy path. Plan = full plan with one `preflight` task. Evaluator passes. Asserts: 1 mission succeeded, 1 episode (INITIAL), 1 attempt PASSED.

2. **`pipeline/episodic_continuation.py`** — `EpisodicContinuation` — partial plan from episode 1 → continuation episode 2. Asserts: 2 episodes, episode 2 has `creation_reason=PARTIAL_CONTINUATION`.

3. **`pipeline/attempt_retry_evaluator_failure.py`** — `AttemptRetryEvaluatorFailure` — attempt 1 fails (evaluator), attempt 2 passes. Asserts: 2 attempts in episode 1, attempt 1 `fail_reason="evaluator_failed"`, attempt 2 PASSED.

4. **`pipeline/dependency_dag_serial.py`** — `DependencyDagSerial` — plan with `a → b → c` (serial chain), each runs `preflight`. Asserts: tasks fired in dependency order, `b.needs == [a.id]`, `c.needs == [b.id]`.

5. **`pipeline/dependency_dag_mixed.py`** — `DependencyDagMixed` — plan with `a → (b,c) → d → (e,f) → g`. Exercises multi-parent fan-in (`d` waits on both `b` AND `c`; `g` waits on both `e` AND `f`) plus parallel sibling dispatch (`b/c`, `e/f`). Asserts: 7 generator nodes invoked, mission succeeded.

6. **`pipeline/generator_failure_quiescence.py`** — `GeneratorFailureQuiescence` — three parallel root tasks `a/b/c` feeding a final `d`. On attempt 1, `b` calls `submit_execution_failure`; the dispatcher waits for `a` and `c` to reach a terminal state (quiescence) before closing the attempt — `d` is marked BLOCKED and never runs. Attempt 2 runs the same plan cleanly (executor branches on `attempt_sequence_no`). Asserts: 1 mission succeeded, 1 episode, 2 attempts; attempt 1 has `EXECUTOR_FAILURE` for `b` plus `EXECUTOR_SUCCESS` for `a` and `c`; attempt 2 has 4 successful executor invocations.

7. **`pipeline/attempt_budget_exhausted.py`** — `AttemptBudgetExhausted` — single generator task always calls `submit_execution_failure`. Both attempts fail; `EpisodeManager` finds no remaining budget; episode closes failed; mission closes failed. Asserts: mission `status=failed`, exactly 2 attempts both `fail_reason="generator_failed"`, no `EVALUATOR_INVOKED` event ever fires.

8. **`sandbox/occ_concurrent_conflicts.py`** — `OccConcurrentConflicts` — single attempt that runs `sandbox_integrity` action (already exercises OCC write/edit/conflict). Asserts: `SANDBOX_BATCH_EDIT_APPLIED` + `SANDBOX_CONFLICT_DETECTED` events present in event sequence.

9. **`planner_validation/duplicate_local_id.py`** — `PlannerDuplicateLocalId` — planner returns plan with two tasks sharing `id="dup"`. Asserts: attempt closes with `fail_reason="planner_failed"`, no generator launched, attempt budget exhausted.

These nine establish:
- single-attempt pipeline state-machine driving (1)
- multi-attempt retry on evaluator vs generator failure (3, 6, 7)
- attempt budget exhaustion (max-retry) (7)
- DAG dependency assertion patterns (4, 5)
- dispatcher quiescence semantics under partial generator failure (6)
- episodic continuation (2)
- sandbox event assertion (8)
- planner validation rejection (9)

To support generator-failure scenarios, the squad runner gained a new `fail:<reason>` executor action that calls `submit_execution_failure` and emits `EventType.EXECUTOR_FAILURE`. Use it whenever a scenario needs a deterministic generator-stage terminal failure (without going through the verifier-injection mutable-state pattern, which is reserved for verifier-stage scenarios).

Other scenarios in the taxonomy follow these patterns. New contributors copy a reference, change the planner/evaluator decisions and `expected_event_sequence`, add a paired test.

## Migration follow-ups

1. **Move composite scenarios into `composite/` subpackage.** Currently `correctness_testing.py` etc. live at the top level for historical reasons. Migration moves them into `composite/`, updates `SCENARIO_REGISTRY`, and updates `live_e2e/tests/sweevo/` test imports. Out of scope for the scaffolding PR — composite scenarios stay where they are until the rest of the focused scenarios land.
2. **Generic sandbox provisioner.** Add `live_e2e/generic_adapter.py` so non-SWE-EVO scenarios don't require a SWE-EVO dataset instance. Tracked separately.
3. **Tier integration.** Update `backend/tests/live_e2e_test/_tools/tiers.toml` to add tiers for `pipeline`, `sandbox`, `tools`, `context`, `planner_validation` so each subpackage runs as its own tier with parallel execution.
4. **Event-type expansion.** Some scenarios (e.g. notification_*) need `EventType.NOTIFICATION_FIRED` which doesn't yet exist. Add as the relevant scenario lands.
5. **Prompt-inspector hooks.** Context-recipe scenarios need a structured way to capture rendered `LaunchBundle` per role. Extend `squad/prompt_inspector.py` to expose typed assertions on packet block kinds + headings.

## Cross-references

- [[live-e2e-testing-framework-design]] — the harness this suite drives
- [[live-e2e-scenarios-full-migration]] — physical layout invariants
- [[task-center-pipeline]] — what `pipeline/` scenarios assert on
- [[sandbox-subsystem]] — what `sandbox/` scenarios drive
- [[tools-hooks-guardrails-agents-notifications-messages]] — what `tools/` scenarios verify
- [[context-engine-recipes]] — what `context/` scenarios assert on
