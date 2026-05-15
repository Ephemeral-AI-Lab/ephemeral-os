# Live E2E Scenarios

Concrete scenarios that drive the live-e2e harness. See
`docs/wiki/live-e2e-scenario-suite-design.md` for the full taxonomy, naming
conventions, and per-subpackage coverage matrix.

## Layout

- `base.py` — `Scenario` protocol, `ScenarioBase`, `ScenarioContext`, `ToolCallSpec`.
- `_utils/` — shared helpers (plan factories, goal/recursive predicates, task_input parsers).
- `pipeline/` — task_center state-machine scenarios (goal/iteration/attempt control flow).
- `sandbox/` — sandbox subsystem scenarios (OCC, overlay, layerstack, LSP, daemon).
- `capacity/` — composite scenarios that intentionally span multiple subsystem owners.
- `tools/` — tool execution, gate hooks, notifications, max-step.
- `context/` — context engine recipe rendering.
- `planner_validation/` — invalid plan rejection.
- `correctness_testing.py`, `full_case_user_input.py`, `full_stack_adversarial.py` —
  composite end-to-end scenarios (existing). Slated to move under `composite/`
  in a follow-up; left at the top level for now.

## Adding a scenario

1. Pick the right subpackage from the taxonomy in the wiki design doc.
2. Copy the closest reference scenario:
   - State-machine assertion → `pipeline/initial_mission.py`
   - DAG dependency assertion → `pipeline/dependency_dag_serial.py`
   - Episodic continuation → `pipeline/episodic_continuation.py`
   - Attempt-retry assertion → `pipeline/attempt_retry_evaluator_failure.py`
   - Sandbox event assertion → `sandbox/occ_concurrent_conflicts.py`
   - Planner rejection assertion → `planner_validation/duplicate_local_id.py`
3. Update `SCENARIO_REGISTRY` in `__init__.py`.
4. Add a paired test under `backend/src/live_e2e/tests/<package>/test_<scenario>.py`.
5. Run `.venv/bin/pytest backend/src/live_e2e/tests/test_scenario_suite_imports.py -q`
   to verify protocol conformance and registry membership.
