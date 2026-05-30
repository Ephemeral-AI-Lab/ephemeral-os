# Next Agent Handoff: mock event-source fallback removal follow-up (2026-05-30)

Read this after `docs/plans/mock_event_source_HANDOFF_2026-05-30.md`. That file
is still the source of truth for Items 3/4/5 design detail; this file is the
short implementation and testing plan after the fallback-removal pass.

## Current state

- `backend/src/task_center_runner/scenarios/builder.py` now constructs
  `ScenarioLoopRunner` unconditionally. There is no
  `_LEGACY_RUNNER_REQUIRED_SCENARIOS` list and no
  `EOS_MOCK_EVENT_SOURCE_RUNNER` runtime decision.
- `backend/src/task_center_runner/core/runner.py` now registers the active mock
  model unconditionally for mock scenarios because every mock scenario goes
  through the real query loop.
- The previously deferred fallback families have been ported into the
  ScenarioLoopRunner path:
  - complex project build, grep/glob, shell-edit/LSP, and the shared bootstrap
    project-build branch;
  - auto-squash and same-path-conflict fan-out branches;
  - background-tool probes through real `shell(background=True)` plus
    `check_background_task_result` / `wait_background_tasks` /
    `cancel_background_task`;
  - `sandbox.ephemeral_workspace_cancellation`.
- `MockSquadRunner` is still present, but `build_scenario_config` no longer
  selects it. Remaining references are direct helper/contract tests and stale
  documentation, not scenario-runtime fallback routing.
- The fallback-removal gate verified so far:
  - fast contract/import slice: `16 passed`
  - ephemeral cancellation: `1 passed`
  - background heartbeat-loss explicit-id path: `1 passed`
  - background late-cancel race: `1 passed`
  - focused project-build smoke: `1 passed`
  - project-build smoke trio: `3 passed`
  - three-parallel project-build diagnostic: `1 passed`
  - runner contract slice without the old env setup: `18 passed`
- A broad `backend/src/task_center_runner/tests/mock` fail-fast run reached the
  end and reported 3 failures. The user explicitly said to ignore them because
  of concurrent worker activity. Do not treat those failures as blockers unless
  they reproduce after the concurrent agent-profile work settles.

## Dirty-worktree warning

The checkout is expected to be dirty. At the time this handoff was written,
dirty files were concentrated in agent/profile and terminal-routing work:

- `backend/src/agents/definition/*`
- `backend/src/agents/profile/**/*`
- `backend/src/tools/submission/planner/_schemas.py`

Treat those as concurrent-worker changes unless you are explicitly assigned to
terminal-routing. Do not revert them to make mock-event-source tests pass.

## Immediate implementation plan

### 1. Reconfirm the routing baseline

Inspect:

- `backend/src/task_center_runner/scenarios/builder.py`
- `backend/src/task_center_runner/core/runner.py`
- `backend/src/task_center_runner/agent/mock/scenario_loop_runner.py`
- `backend/src/tools/_framework/execution/tool_call.py`

- `build_scenario_config` returns a `ScenarioLoopRunner` factory only;
- no env var or scenario-name branch can select `MockSquadRunner`;
- active mock model setup is unconditional for mock scenarios;
- tool start events and completion events use the same query-loop run id, so
  performance samples get `started_ts` and `duration_ms`.

### 2. Delete the remaining direct `MockSquadRunner` helper surface

The remaining deletion blockers are direct tests/helpers, not scenario runtime:

- `backend/src/task_center_runner/tests/mock/contracts/test_runner_imports.py`
  still instantiates `MockSquadRunner` for prompt-inspection/probe-path
  contracts.
- `backend/src/task_center_runner/tests/mock/contracts/test_advisor_gate_negative_path.py`
  still calls `MockSquadRunner._approve_terminal`.
- Several architecture and historical plan docs still describe
  `MockSquadRunner` as the selected mock runtime.

Convert those focused tests to the new homes before deleting
`backend/src/task_center_runner/agent/mock/runner.py`:

- prompt inspection and initial-message capture should stay on
  `ScenarioLoopRunner` / `prompt_inspector`;
- advisor approval metadata should move to a small test helper that builds the
  same conversation message pair without depending on the old runner class.

### 3. Keep test migrations graph-backed

When a migrated scenario still asserts lifecycle events such as:

- `PLANNER_INVOKED`
- `EXECUTOR_SUCCESS`
- `VERIFIER_FAILURE`
- `EVALUATOR_SUCCESS`
- `RECURSIVE_WORKFLOW_REQUESTED`
- `FULL_STACK_SCRIPT_COMPLETED`

move the assertion to `report.graph_summary` or persisted task/message
artifacts. Keep true sandbox events (`SANDBOX_*`) as event assertions.

Useful already-migrated templates:

- `backend/src/task_center_runner/tests/mock/_focused_scenario_contracts.py`
- `backend/src/task_center_runner/tests/mock/sandbox/full_stack/test_full_stack_adversarial.py`
- `backend/src/task_center_runner/tests/mock/sandbox/capacity/test_full_system_capacity_matrix.py`

### 4. Fan-out promotions are now in the ScenarioLoopRunner path

- `sandbox.auto_squash_commit_resume`
- `sandbox.ephemeral_workspace_same_path_conflict`
- complex project build x6:
  `complex_project_build`, `complex_project_build_smoke`,
  `complex_project_build_shell_edit_lsp`,
  `complex_project_build_shell_edit_lsp_smoke`,
  `complex_project_build_grep_glob`,
  `complex_project_build_grep_glob_smoke`

These should remain graph-backed/fan-out shaped. Keep at least two work
generators concurrent where the scenario is intended to prove fan-out, including
smoke variants. Do not reintroduce a runner fallback to avoid the loop budget;
raise the scenario-local mock executor budget in `ScenarioLoopRunner` when a
deterministic high-volume mock probe needs it.

### 5. Background rewrite is now bridged to the real model

The old blocking `background_task_id` probe contract is bridged through real
loop background calls. Intentional stale-inflight tests pass an internal fixed
sandbox invocation id and disable the supervisor heartbeat for that launched
task; ordinary background tasks still use the normal heartbeat path.

### 6. Phase-D deletion is the next cleanup pass

Scenarios now run through `ScenarioLoopRunner`. The deletion pass should:

- delete `MockSquadRunner` after the direct helper tests are ported;
- remove stale `EOS_MOCK_EVENT_SOURCE_RUNNER` references from active tests and
  maintained architecture docs;
- remove lifecycle-only event assertions and then remove unused lifecycle enum
  members last.

## Testing plan

Run tests in this order. Stop on the first real failure unless it is clearly
from concurrent profile/terminal-routing work.

### Fast import and contract gate

```bash
uv run pytest -q -p no:cacheprovider \
  backend/src/task_center_runner/tests/mock/contracts/test_runner_imports.py \
  backend/src/task_center_runner/tests/mock/contracts/test_scenario_event_source_spike.py \
  backend/src/task_center_runner/tests/mock/contracts/test_scenario_loop_runner_planner_submit.py \
  backend/src/task_center_runner/tests/mock/contracts/test_correctness_via_event_source.py
```

### ScenarioLoopRunner migrated gate

Do not set `EOS_MOCK_EVENT_SOURCE_RUNNER`; it should have no runtime effect.

```bash
uv run pytest -n 3 -p no:cacheprovider \
  backend/src/task_center_runner/tests/mock/contracts \
  backend/src/task_center_runner/tests/mock/task_center/test_full_case_user_input.py \
  backend/src/task_center_runner/tests/mock/sandbox/full_stack/test_full_stack_adversarial.py \
  backend/src/task_center_runner/tests/mock/sandbox/capacity/test_full_system_capacity_matrix.py
```

Expected: `42 passed` unless unrelated concurrent work changes collection.

### Focused regressions from this pass

```bash
uv run pytest -q -p no:cacheprovider \
  backend/src/task_center_runner/tests/mock/sandbox/ephemeral_workspace/test_ephemeral_lowerdir_disk_is_o1_under_100_calls.py \
  backend/src/task_center_runner/tests/mock/sandbox/layer_stack_occ_overlay/test_high_concurrency_layerstack_overlay_occ.py \
  backend/src/task_center_runner/tests/mock/sandbox/layer_stack_occ_overlay/test_focused_sandbox_scenarios.py
```

### Former fallback smoke

```bash
uv run pytest -q -p no:cacheprovider \
  backend/src/task_center_runner/tests/mock/sandbox/background_tool/test_background_engine_restart_no_lease_leak.py \
  backend/src/task_center_runner/tests/mock/sandbox/project_build/test_complex_project_build_shell_edit_lsp_smoke.py \
  backend/src/task_center_runner/tests/mock/sandbox/project_build/test_complex_project_build_grep_glob_smoke.py
```

Also keep the three-parallel project-build diagnostic on the ScenarioLoopRunner
path:

```bash
uv run pytest -q -p no:cacheprovider \
  backend/src/task_center_runner/tests/mock/sandbox/project_build/test_project_build_shell_edit_lsp_three_parallel_agents.py
```

There should be no fallback list. If this test fails, fix the
ScenarioLoopRunner bridge/fan-out path.

### Broad fail-fast suite

```bash
uv run pytest -n 3 -x -p no:cacheprovider --tb=short \
  backend/src/task_center_runner/tests/mock
```

Expected current behavior:

- the suite is long-running;
- skips are normal;
- if failures mention `terminal_routing` / `AgentDefinition`, verify whether the
  concurrent profile-routing work is complete before changing mock-event-source
  code;
- if failures mention `NotImplementedError: executor action ... not yet adapted`,
  implement the action in the event-source path.

### Static checks

```bash
uv run ruff check \
  backend/src/task_center_runner/scenarios/builder.py \
  backend/src/task_center_runner/core/runner.py \
  backend/src/task_center_runner/agent/mock/scenario_loop_runner.py \
  backend/src/task_center_runner/agent/mock/scenario_adapter.py \
  backend/src/task_center_runner/agent/mock/probe_bridge.py \
  backend/src/tools/_framework/execution/tool_call.py \
  backend/src/task_center_runner/tests/mock

git diff --check
```

## Reporting expectations

The next agent should report:

- proof that no scenario relies on `_LEGACY_RUNNER_REQUIRED_SCENARIOS`;
- whether `MockSquadRunner` is still directly referenced by tests/docs and why;
- exact pytest commands run and pass/fail counts;
- exact `.sweevo_runs/scenario_logs/...` paths for any live failures inspected;
- any failures ignored because they are owned by concurrent agent-profile work.
