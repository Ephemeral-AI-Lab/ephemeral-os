# Mock Sandbox Iteration Report

## Iteration 1 - 2026-05-26 13:29:44 CST

- Exact command run:
  `uv run pytest -q -x --tb=short --durations=20 /Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/tests/mock/sandbox`
- Exact run directory or artifact paths inspected: no `.sweevo_runs/scenario_logs/**/run.json` was created before failure; inspected Docker container `5e8196e60955` and `/tmp/eos-sandbox-runtime/runtime.log`.
- Pass/fail/skip status: failed during fixture setup before the first test body.
- Findings summary: The reused sandbox first returned invalid daemon JSON, then a fresh sandbox failed with `RuntimeExecFailed: sandbox daemon failed to bind socket within 10s`.
- Issues found: The daemon log in fresh container `5e8196e60955` shows startup crashed before socket bind with `ImportError: cannot import name 'StrEnum' from 'enum' (/usr/lib/python3.10/enum.py)`.
- Why it failed: Root cause is a Python-version contract violation in daemon-imported code. `backend/src/sandbox/overlay/namespace_entrypoint.py` imports `enum.StrEnum`, but the SWE-EVO sandbox selected Python 3.10 and this project supports Python `>=3.10`; `StrEnum` is Python 3.11+.
- Fix applied: Changed `backend/src/sandbox/overlay/namespace_entrypoint.py` so `WorkspaceMountMode` inherits from `str, Enum` instead of `StrEnum`, and added `__str__` to preserve the old value stringification behavior.
- Verification result after the fix: `uv run pytest backend/tests/unit_test/test_sandbox/test_execution/test_strategies/test_namespace_entrypoint.py -q` passed: 8 passed in 0.11s. `uv run pytest -q -x --tb=short --durations=20 backend/src/task_center_runner/tests/mock/sandbox/background_tool/test_background_engine_restart_no_lease_leak.py` passed: 1 passed in 52.13s. Run directory: `.sweevo_runs/scenario_logs/sandbox.background_engine_restart_no_lease_leak/20260526T053335Z_23cf68fda8fd`.
- Remaining risk or next iteration target: The targeted scenario report produced complete V3 sections and drop-free daemon pull stats, but §13 included `audit.events_count_drift` because the warning compared total mixed JSONL rows to daemon-only `events_pulled`. Fixed `backend/src/task_center_runner/audit/performance_report.py` to compare daemon-pulled JSONL rows only, and added `test_d8_events_count_drift_ignores_host_side_rows`.

## Iteration 2 - 2026-05-26 13:36:21 CST

- Exact command run:
  `uv run pytest backend/tests/unit_test/test_task_center_runner/test_performance_report_deferrals.py backend/tests/unit_test/test_task_center_runner/test_performance_report_v3.py backend/tests/unit_test/test_sandbox/test_execution/test_strategies/test_namespace_entrypoint.py -q`
- Exact run directory or artifact paths inspected: `.sweevo_runs/scenario_logs/sandbox.background_engine_restart_no_lease_leak/20260526T053335Z_23cf68fda8fd/performance_report.json` and `sandbox_events.jsonl`.
- Pass/fail/skip status: passed; 47 passed in 0.58s.
- Findings summary: Report unit coverage now preserves the D8 warning for real daemon row drift and suppresses false drift from host-side sandbox rows coexisting in `sandbox_events.jsonl`.
- Issues found: Existing targeted run artifact still contains the old warning because it was generated before the report fix.
- Why it failed: The report builder used total JSONL rows for the drift comparison, even though host-side rows from the stream bridge are not counted by daemon puller `events_pulled`.
- Fix applied: `backend/src/task_center_runner/audit/performance_report.py` now compares `events_pulled` against rows with `schema == "sandbox.daemon.audit.pull.v1"`; `backend/tests/unit_test/test_task_center_runner/test_performance_report_deferrals.py` covers mixed host/daemon artifacts.
- Verification result after the fix: focused report and namespace-entrypoint units passed. Regenerated targeted scenario with `uv run pytest -q -x --tb=short --durations=20 backend/src/task_center_runner/tests/mock/sandbox/background_tool/test_background_engine_restart_no_lease_leak.py`; it passed: 1 passed in 24.34s. Run directory: `.sweevo_runs/scenario_logs/sandbox.background_engine_restart_no_lease_leak/20260526T053653Z_7f217437e12d`.
- Remaining risk or next iteration target: The regenerated V3 report has all required sections, `events_pulled=15`, `dropped_event_count=0`, `lost_before_seq=0`, max buffer pressure about `0.00058`, live artifact size 38,364 bytes, O(1) workspace bytes/truncation all zero, and no `audit.events_count_drift`. It still reports `occ.conflict_cluster` for two typed accepted OCC conflicts, which is expected for this engine-abandon/recovery scenario. Resume the full mock sandbox directory.

## Iteration 3 - 2026-05-26 13:43:06 CST

- Exact command run:
  `uv run pytest -q -x --tb=short --durations=20 /Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/tests/mock/sandbox`
- Exact run directory or artifact paths inspected: `.sweevo_runs/scenario_logs/full_stack_adversarial/20260526T054210Z_5b37b55067b8`.
- Pass/fail/skip status: failed after 26 passed in 304.36s.
- Findings summary: The full run progressed through background, capacity, and ephemeral workspace scenarios. Daemon audit pull stayed drop-free in inspected reports; typed OCC conflict warnings appeared in conflict-oriented scenarios as expected.
- Issues found: `test_full_stack_adversarial_runs_agent_tool_script_matrix` failed in `_assert_sandbox_monitor_events` with `ValueError: 'daemon.started' is not a valid EventType`.
- Why it failed: The test casts every `sandbox_events.jsonl` row to runner `EventType`, but `sandbox_events.jsonl` now includes daemon-pulled audit rows such as `daemon.started`, `occ.changeset_prepared`, and `overlay_workspace.mounted`. These are valid daemon audit event strings, not members of the runner in-memory audit enum.
- Fix applied: Updated `backend/src/task_center_runner/tests/mock/sandbox/full_stack/test_full_stack_adversarial.py::_assert_sandbox_monitor_events` to filter persisted JSONL rows to known runner `EventType` values before casting them.
- Verification result after the fix: `uv run pytest -q -x --tb=short --durations=20 backend/src/task_center_runner/tests/mock/sandbox/full_stack/test_full_stack_adversarial.py::test_full_stack_adversarial_runs_agent_tool_script_matrix` passed: 1 passed in 38.65s. Run directory: `.sweevo_runs/scenario_logs/full_stack_adversarial/20260526T054418Z_08e88457f12f`.
- Remaining risk or next iteration target: The regenerated full-stack report has V3 sections, `events_pulled=2063`, `dropped_event_count=0`, `lost_before_seq=0`, max buffer pressure about `0.072`, live artifact size 2,818,157 bytes, O(1) workspace bytes/truncation zero, and only the expected synthetic `occ.conflict_cluster` warning. Resume the full mock sandbox directory.

## Iteration 4 - 2026-05-26 14:00:30 CST

- Exact command run:
  `uv run pytest -q -x --tb=short --durations=20 /Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/tests/mock/sandbox`
- Exact run directory or artifact paths inspected: `.sweevo_runs/scenario_logs/sandbox.auto_squash_commit_resume/20260526T055957Z_18c24ce87322`.
- Pass/fail/skip status: failed after 117 passed and 6 skipped in 890.45s.
- Findings summary: The full run reached isolated-workspace stress tiers and then `sandbox.auto_squash_commit_resume`. The latest auto-squash artifact was complete enough to inspect: `events_pulled=806`, `dropped_event_count=0`, `lost_before_seq=0`, max buffer pressure about `0.036`, live artifact size 1,337,186 bytes.
- Issues found: `test_auto_squash_commit_resume_crosses_depth_threshold` failed with `ValueError: 'daemon.started' is not a valid EventType`.
- Why it failed: Same mixed JSONL contract issue as full-stack: test code assumed every persisted sandbox row is a runner audit `EventType`, but daemon-pulled rows are valid daemon event strings. A repo search found the same raw cast in auto-squash, project-build contracts, and an adjacent task-center mock test.
- Fix applied: Filter persisted JSONL rows to known runner `EventType` values in `backend/src/task_center_runner/tests/mock/sandbox/layer_stack_occ_overlay/test_auto_squash_commit_resume.py`, `backend/src/task_center_runner/tests/mock/_project_build_contracts.py`, and `backend/src/task_center_runner/tests/mock/task_center/test_full_case_user_input.py`.
- Verification result after the fix: `uv run pytest -q -x --tb=short --durations=20 backend/src/task_center_runner/tests/mock/sandbox/layer_stack_occ_overlay/test_auto_squash_commit_resume.py::test_auto_squash_commit_resume_crosses_depth_threshold` passed: 1 passed in 20.93s. Run directory: `.sweevo_runs/scenario_logs/sandbox.auto_squash_commit_resume/20260526T060131Z_5c122634d087`. Focused report/entrypoint unit bundle also passed: 47 passed in 0.43s.
- Remaining risk or next iteration target: The regenerated auto-squash report has V3 sections, `events_pulled=1028`, `dropped_event_count=0`, `lost_before_seq=0`, max buffer pressure about `0.043`, live artifact size 1,434,524 bytes, and only the expected typed `occ.conflict_cluster` warning. Resume the full mock sandbox directory.

## Iteration 5 - 2026-05-26 14:57:51 CST

- Exact command run:
  `uv run pytest -q -x --tb=short --durations=20 /Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/tests/mock/sandbox`
- Exact run directory or artifact paths inspected: final scenario `.sweevo_runs/scenario_logs/sandbox.complex_project_build_shell_edit_lsp_three_parallel_agents/20260526T064750Z_89adb2d593ab`; also sampled intermediate background, capacity, full-stack, auto-squash, and project-build run directories during the run.
- Pass/fail/skip status: passed; 157 passed, 7 skipped in 3284.17s.
- Findings summary: The full mock sandbox directory now passes end to end. The final scenario reported `events_pulled=28099`, `dropped_event_count=0`, `lost_before_seq=0`, `daemon_restarts_observed=0`, `puller_attached=true`, artifact live bytes `26780923`, and no rotations.
- Issues found: The final scenario has V3 warnings `audit.pressure` (`max_buffer_pressure=0.9924927949905396`), `audit.floor_escalated` (`floor_raises=6`), and expected `occ.conflict_cluster` (`6952` typed conflicts). No events were dropped or lost, and the artifact-bound/drop-free gates passed.
- Why it failed: No test failure remained. The residual audit pressure warning is a performance headroom signal under the largest three-agent project-build workload, not a correctness failure in this pass. Existing daemon-pull tests and docs currently model `floor_raises` as expected under sustained pressure, so changing puller cadence is left as follow-up rather than bundled into this test-fix iteration.
- Fix applied: none in this iteration; it validated the fixes from iterations 1-4.
- Verification result after the fix: full directory pass above; `uv run ruff check backend/src/sandbox/overlay/namespace_entrypoint.py backend/src/task_center_runner/audit/performance_report.py backend/tests/unit_test/test_task_center_runner/test_performance_report_deferrals.py backend/src/task_center_runner/tests/mock/sandbox/full_stack/test_full_stack_adversarial.py backend/src/task_center_runner/tests/mock/sandbox/layer_stack_occ_overlay/test_auto_squash_commit_resume.py backend/src/task_center_runner/tests/mock/_project_build_contracts.py backend/src/task_center_runner/tests/mock/task_center/test_full_case_user_input.py` passed; `git diff --check` passed.
- Remaining risk or next iteration target: If this becomes a release-gate task rather than a test-pass task, investigate reducing daemon audit ring pressure in `DaemonAuditPuller` for the three-agent project-build workload while preserving the existing floor semantics and tests.
