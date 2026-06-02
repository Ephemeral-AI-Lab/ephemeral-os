# Test Runner Migration Iteration Report

Plan: `docs/plans/test_runner_migration_PLAN.md`

## Iteration 1 - 2026-06-02 08:00:57 +0800 CST

Checkout summary:
- HEAD: `09ecd7e66 Refresh test runner naming and docs`
- Worktree note: concurrent sandbox/plugin edits were already present and are outside this migration loop.

Target files:
- `backend/src/test_runner`
- `backend/tests/unit_test/test_test_runner`
- `backend/src/test_runner/tests/mock`
- `backend/tests/unit_test/test_benchmarks`
- `docs/architecture/test_runner`
- `scripts/build_initial_messages_report.py`
- `scripts/regen_initial_messages_cases_gaps.py`

Findings and issues:
- The renamed `test_runner` package was importable, and the legacy `task_center_runner` package was no longer importable after ignored stale directories were removed.
- `AuditRecorder` still depended on the removed `workflow._core.primitives.attempt_id_from_task_id` helper. The current task-first contract stores `TaskRecord.attempt_id`; recorder resolution should use that persisted field.
- Focused recorder and protocol tests exposed a typo in the scheduler path: `asyncio.get_requestning_loop()` should be `asyncio.get_running_loop()`.
- Active runner vocabulary still contained stale handoff/root-child workflow names in scenario fields, action tokens, generated-report scripts, and architecture text.
- Contract/request collection hit a syntax error in `backend/src/test_runner/agent/mock/probes.py`: duplicate `task_id` and `request_id` keyword arguments in a `SandboxCaller(...)` construction.
- A combined contracts plus request suite printed 11 passing dots and then stopped producing output. The run was terminated to avoid leaving a stale pytest session.

Fixes applied:
- Resolved audit task directories through `TaskRecord.attempt_id` instead of parsing task ids.
- Updated benchmark audit recorder fixtures to store explicit `attempt_id` values on inserted `TaskRecord` rows.
- Corrected the scheduler typo to call `asyncio.get_running_loop()`.
- Renamed stale scenario and report vocabulary from recursive handoff/root-child workflow wording to delegated workflow/request wording.
- Removed duplicate `SandboxCaller(...)` keyword arguments from the mock probe builder.

Commands run:
- `uv run python - <<'PY' ... import test_runner ... importlib.util.find_spec('task_center_runner') ... PY`
  - Result: passed; printed `test_runner create_per_test_task_stores` and `None`.
- `uv run pytest -q backend/tests/unit_test/test_test_runner/test_run_report_structural_golden.py backend/tests/unit_test/test_test_runner/test_protocols.py backend/tests/unit_test/test_test_runner/test_no_core_imports.py backend/src/test_runner/tests/mock/request/test_stores.py backend/tests/unit_test/test_benchmarks/test_sweevo_audit_recorder.py backend/tests/unit_test/test_benchmarks/test_sweevo_sandbox_event_monitor.py --collect-only`
  - Result: passed; collected 35 tests.
- `uv run pytest -q backend/tests/unit_test/test_test_runner/test_run_report_structural_golden.py backend/tests/unit_test/test_test_runner/test_protocols.py backend/tests/unit_test/test_test_runner/test_no_core_imports.py backend/src/test_runner/tests/mock/request/test_stores.py backend/tests/unit_test/test_benchmarks/test_sweevo_audit_recorder.py backend/tests/unit_test/test_benchmarks/test_sweevo_sandbox_event_monitor.py`
  - Result: first failed on the scheduler typo, then passed after the fix with 35 tests.
- `rg -n "recursive_handoff_goal|request_recursive_workflow|request_recursive_matrix|goal_handoff|submit_workflow_handoff|WAITING_WORKFLOW|root workflow|child workflow|handoff|context_message" backend/src/test_runner backend/tests/unit_test/test_test_runner docs/architecture/test_runner scripts/build_initial_messages_report.py scripts/regen_initial_messages_cases_gaps.py`
  - Result: passed after vocabulary fixes; no active-scope matches.
- `uv run pytest -q backend/src/test_runner/tests/mock/contracts backend/src/test_runner/tests/mock/request`
  - Result: first failed at collection on duplicate keywords in `probes.py`; after the fix, a rerun stalled after 11 dots and was killed.

Fresh artifacts inspected:
- No fresh `.sweevo_runs` live artifact directory was produced in this iteration.
- Process state was inspected after the stalled run; pytest PIDs `90141` and `90159` were terminated and no pytest process remained.

Current verdict:
- Correctness: focused import, collect-only, benchmark audit recorder, protocol, and structural golden checks passed after fixes.
- Correctness gap: contracts/request suites still need narrowed reruns to locate the stalled test.
- Performance: no O(1) memory/disk or latency verdict claimed yet; no fresh live sandbox artifacts were available for inspection.

Next iteration entry point:
- Run `backend/src/test_runner/tests/mock/contracts` separately.
- Run `backend/src/test_runner/tests/mock/request` with narrower selection and verbose output to identify the stall.
- Run the smallest available live sandbox E2E smoke, then append fresh artifact paths, audit fields, correctness results, and performance observations.

## Iteration 2 - 2026-06-02 08:00:57 +0800 CST

Updated scope:
- User narrowed the active goal to make `backend/src/test_runner/tests/mock` work.
- `backend/src/test_runner/tests/real_agent` and real-LLM tests are out of scope for this loop.

Target files:
- `backend/src/test_runner/tests/mock/contracts`
- `backend/src/test_runner/environments/sweevo_image/fixtures.py`
- `backend/src/test_runner/benchmarks/sweevo/_provision.py`

Findings and issues:
- The stale-vocabulary grep gate for active runner paths passed after Iteration 1 fixes.
- Running contracts separately no longer hit the duplicate-keyword syntax error.
- Five contract tests failed during live sandbox fixture setup before their mock-runner assertions ran.
- The first failure signal was `sandbox.host.daemon_client._DaemonDispatchError: internal_error: plugin runtime warm failed for 'lsp':` from `setup_sweevo_sandbox(...)`.
- The failures are setup-gate failures in the live SWE-EVO image fixture, not real-agent or real-LLM behavior.

Commands run:
- `uv run pytest -q backend/src/test_runner/tests/mock/contracts`
  - Result: failed; 34 passed and 5 setup errors from `plugin runtime warm failed for 'lsp'`.

Fresh artifacts inspected:
- Pytest traceback only. No fresh `.sweevo_runs` artifact directory was identified in this iteration yet.

Fixes applied:
- None yet in this iteration. Next step is to inspect whether the live fixture should skip or quarantine provider warm failures for mock-contract runs.

Current verdict:
- Correctness: offline contract tests are mostly passing, but the suite is not green because live-provider setup errors are not gated.
- Performance: no O(1) memory/disk or latency verdict claimed from this failed setup.

## Iteration 3 - 2026-06-02 08:44:15 +0800 CST

Updated scope:
- Keep `backend/src/test_runner/tests/real_agent` and real-LLM tests out of this loop.
- Treat `backend/src/test_runner/tests/mock/sandbox` and probe-heavy live request scenarios as Rust-runtime lanes, matching Phase D of the migration plan.

Target files:
- `backend/src/test_runner/agent/mock/scenario_adapter.py`
- `backend/src/sandbox/daemon/builtin_operations.py`
- `backend/src/test_runner/tests/_live_config.py`
- `backend/src/test_runner/tests/mock/conftest.py`
- `backend/src/test_runner/tests/mock/contracts`
- `backend/src/test_runner/tests/mock/request`

Findings and issues:
- The root mock script polled `check_workflow_status` too quickly and looked for stale terminal strings. The live tool returns `succeeded`, `failed`, or `cancelled`; a completed delegated workflow was being polled until budget exhaustion and then cancelled.
- The Task-first root id shape is now `root-...`; the planner proof still asserted the old `:root` suffix.
- Python-daemon `exec_command` compatibility flattened lower-level shell dispatch errors into `status=error`, `exit_code=0`, and empty output, which made the old overlay mount failure hard to diagnose.
- Default-runtime request live scenarios still hit the old Python overlay shell path: `OSError: [Errno 22] Invalid argument: "b'upperdir'=..."`.
- With `EOS_SANDBOX_RUNTIME=rust`, live setup failed before tests ran because the local ignored artifact hash did not match the host pin: local `sandbox/dist/eosd-linux-amd64` rebuilt to `9be4e5a23d62d19002e3f14abc580c7c7fe63fa7bd663d77d21fcd333aa686a0`, while `sandbox.host.runtime_artifact.EOSD_SHA256["amd64"]` expects `81eb221542666647a3b0a80a0ed254dff674a0ead27d814bfcea26bd14996d53`.

Fixes applied:
- Added a short async sleep between root delegated-workflow status polls and treated `succeeded` as a terminal workflow status.
- Updated the planner proof assertion to accept the current `root-...` parent Task id shape.
- Preserved lower-level error details and timings in Python-daemon `exec_command` compatibility responses.
- Added a Rust artifact readiness helper and used it to skip Rust command/session and live request/sandbox lanes before fixture setup when the runtime is not selected or the pinned `eosd` artifact is unavailable.

Commands run:
- `uv run python backend/scripts/build_upload_eosd_docker.py --arch amd64`
  - Result: passed Docker upload verification, but rebuilt local artifact SHA was `9be4e5a23d62d19002e3f14abc580c7c7fe63fa7bd663d77d21fcd333aa686a0`, not the checked host pin.
- `uv run pytest -q backend/src/test_runner/tests/mock/contracts --tb=short --durations=10`
  - Result: passed with `36 passed, 3 skipped`.
- `uv run pytest -q backend/src/test_runner/tests/mock/request --tb=short --durations=10`
  - Result: passed with `4 passed, 23 skipped`.
- `env EOS_SANDBOX_PROVIDER=docker EOS_SANDBOX_RUNTIME=rust uv run pytest -q backend/src/test_runner/tests/mock/contracts/test_scenario_event_source_spike.py::test_foreground_tool_effect_and_budget_through_real_loop backend/src/test_runner/tests/mock/request/test_focused_scenarios.py::test_focused_reference_scenario_runs --tb=short --durations=5`
  - Result: skipped before fixture setup with `19 skipped` because the local `eosd` artifact hash does not match the host pin.
- `uv run pytest -q backend/src/test_runner/tests/mock --tb=short --durations=10`
  - Result: passed with `43 passed, 194 skipped`.
- `uv run pytest -q backend/tests/unit_test/test_config/test_central_loader.py backend/tests/unit_test/test_test_runner/test_run_report_structural_golden.py backend/tests/unit_test/test_test_runner/test_protocols.py backend/tests/unit_test/test_test_runner/test_no_core_imports.py backend/tests/unit_test/test_benchmarks/test_sweevo_audit_recorder.py backend/tests/unit_test/test_benchmarks/test_sweevo_sandbox_event_monitor.py`
  - Result: passed with `40 passed`.
- `uv run ruff check ...`
  - Result: passed.

Fresh artifacts inspected:
- `.sweevo_runs/scenario_logs/planner_submit_proof/20260602T003319Z_c24618765b98`
  - Showed completed planner/executor/reducer tasks, then root cancellation after polling budget exhaustion.
- `.sweevo_runs/scenario_logs/planner_submit_proof/20260602T004318Z_df828896abf2`
  - Latest broad mock run artifact after the root polling/status fix.
- `.sweevo_runs/scenario_logs/pipeline.initial_workflow/20260602T004107Z_ea330a8ad094`
  - Captured the Python overlay mount failure from default-runtime `exec_command`.
- `sandbox/dist/eosd-linux-amd64`
  - Ignored generated artifact rebuilt successfully, but its SHA is not the checked host pin.

Current verdict:
- Correctness: mock contracts and request suites pass in the current checkout, with Rust live lanes explicitly skipped until the pinned `eosd` artifact is restored.
- Correctness gap: skipped Rust command/session, live request, and sandbox suites still need a rerun after the local artifact is rebuilt to the checked pin or the pin is deliberately updated with the coordinated Rust changes.
- Performance: no O(1) memory/disk or latency verdict is claimed for the skipped Rust live lanes. The broad mock run only proves collection/gating plus the default-runtime non-command contracts.

## Iteration 4 - 2026-06-02 12:18:00 +0800 CST

Updated scope:
- Mid-flight user correction: plugin operation serialization is forbidden. The fix must enable concurrent plugin operations and refine the overlay/PPC mechanism for same-service concurrency.
- Continued to keep `backend/src/test_runner/tests/real_agent` and real-LLM tests out of scope.

Target files:
- `sandbox/crates/eos-daemon/src/plugin/mod.rs`
- `sandbox/crates/eos-daemon/src/plugin/ppc_router.rs`
- `backend/src/sandbox/ephemeral_workspace/plugin/ppc_service.py`
- `backend/src/sandbox/ephemeral_workspace/plugin/op_context.py`
- `backend/scripts/bench_rust_daemon_plugin.py`
- `backend/tests/unit_test/test_sandbox/test_plugin_ppc_service.py`
- `backend/tests/live_e2e_test/sandbox/plugin/test_plugin_refresh_strategies.py`
- `backend/src/sandbox/host/runtime_artifact/__init__.py`
- `docs/plans/test_runner_migration_PLAN.md`
- `docs/plans/sandbox-plugin-service-adversarial-plan.md`
- `docs/plans/sandbox-rust-external-migration-PROGRESS.md`

Findings and issues:
- The docs still said same-service read-only plugin calls serialize on a shared client, which now violates the intended contract.
- The Rust daemon PPC path needed a high-level regression proving the second same-service request reaches the service before the first reply is released.
- The Python PPC bridge needed to avoid request-loop serialization, keep sync handlers off the event loop, cache handler imports safely, route callback replies by message id, and preserve per-operation manifest/layer-stack context for concurrent callbacks.
- Live direct Rust plugin validation initially failed because the reusable PPC bridge service was not bundled into `/eos/daemon`, then because Docker `put_archive` could not target a not-yet-existing nested `/eos/daemon` path. The benchmark installer now stages those files under `/tmp` and finalizes them with the same shell-copy path used by the harness scripts.
- The combined `test_plugin_refresh_strategies.py` command with `EOS_SANDBOX_RUNTIME=rust` failed before reaching the Rust plugin benchmark because the older refresh-strategy prelude calls Python-daemon-only `api.acquire_snapshot`. The relevant Rust PPC live gate was run directly with `bench_rust_daemon_plugin.py`.

Fixes applied:
- Updated plan/progress docs to forbid plugin op serialization and describe the refined contract: shared service connection, short write lock only, pending reply map, dedicated reader thread, message-id routed out-of-order replies, and `parent_message_id` for concurrent callback-capable operations.
- Strengthened the daemon route test so it waits for the second request before releasing the first reply; this fails if same-service ops serialize behind the first in-flight request.
- Updated the Python PPC bridge to spawn a task per service request, write frames under a write lock, resolve callback futures by message id, run sync handlers in a worker thread, cache handler imports, and capture per-operation context for mounted-workspace callbacks.
- Made `op_context` avoid runtime imports of overlay/event modules that are type-only for the reusable bridge.
- Bundled the minimal Python PPC bridge runtime into the live Rust plugin benchmark and added live concurrent runtime-bridge delay/apply probes.
- Rebuilt and uploaded the amd64 `eosd` artifact and pinned `EOSD_SHA256["amd64"]` to `6d58b54f40cdaa8af77a767983dda0b06c27ea0cb4221d781b2b4cce42c431c4`.

Commands run:
- `uv run pytest -q backend/tests/unit_test/test_sandbox/test_plugin_ppc_service.py --tb=short`
  - Result: passed with `3 passed`.
- `uv run ruff check backend/src/sandbox/ephemeral_workspace/plugin/op_context.py backend/src/sandbox/ephemeral_workspace/plugin/ppc_service.py backend/tests/unit_test/test_sandbox/test_plugin_ppc_service.py backend/scripts/bench_rust_daemon_plugin.py backend/tests/live_e2e_test/sandbox/plugin/test_plugin_refresh_strategies.py backend/src/sandbox/host/runtime_artifact/__init__.py`
  - Result: passed.
- `cargo fmt --all --check`
  - Result: passed after applying `cargo fmt --all`.
- `cargo test -p eos-daemon plugin -- --test-threads=1`
  - Result: passed with `34 passed`.
- `cargo test -p eos-plugin -p eos-daemon --lib`
  - Result: passed with `58` daemon tests and `18` plugin tests.
- `uv run python backend/scripts/build_upload_eosd_docker.py --arch amd64`
  - Result: passed; wrote `bench/local-eosd-amd64-upload.json`.
- `env EOS_SANDBOX_PROVIDER=docker EOS_SANDBOX_RUNTIME=rust uv run pytest -q backend/tests/live_e2e_test/sandbox/plugin/test_plugin_refresh_strategies.py::test_plugin_workspace_snapshot_refresh_strategy --tb=short --durations=10`
  - Result: skipped because `EOS_LIVE_E2E_IMAGE` was unset.
- `env EOS_SANDBOX_PROVIDER=docker EOS_SANDBOX_RUNTIME=rust EOS_LIVE_E2E_IMAGE=sweevo-dask__dask-10042:latest uv run pytest -q backend/tests/live_e2e_test/sandbox/plugin/test_plugin_refresh_strategies.py::test_plugin_workspace_snapshot_refresh_strategy --tb=short --durations=10`
  - Result: failed before Rust plugin benchmark on `unknown op: api.acquire_snapshot` from the Python refresh-strategy prelude.
- `env EOS_SANDBOX_RUNTIME=rust uv run python backend/scripts/bench_rust_daemon_plugin.py --docker-image sweevo-dask__dask-10042:latest --report .omc/results/rust-daemon-plugin-generic-20260602T041506Z-concurrent-ppc.json --markdown-report .omc/results/rust-daemon-plugin-generic-20260602T041506Z-concurrent-ppc.md`
  - Result: passed with `gate_pass=True`.

Fresh artifacts inspected:
- `bench/local-eosd-amd64-upload.json`
- `.omc/results/rust-daemon-plugin-generic-20260602T041310Z-concurrent-ppc.json`
- `.omc/results/rust-daemon-plugin-generic-20260602T041351Z-concurrent-ppc-keep.json`
- `.omc/results/rust-daemon-plugin-generic-20260602T041506Z-concurrent-ppc.json`
- `.omc/results/rust-daemon-plugin-generic-20260602T041506Z-concurrent-ppc.md`
- Retained failed Docker container `2b00b73d5539...` was inspected to confirm the missing bridge bundle, then removed.

Current verdict:
- Correctness: PASS for the plugin PPC concurrency slice. The passing live artifact has `gate_pass=true`; `runtime_bridge_concurrent` shows `fast-second` finished at the service and client before delayed `slow-first`; both replies came through the reusable PPC bridge with `workspace_mounted=true`.
- Concurrent write/callback correctness: PASS. `runtime_bridge_concurrent_apply` concurrently committed `live_plugin_runtime_bridge_concurrent_a.txt` and `live_plugin_runtime_bridge_concurrent_b.txt` through mounted-workspace OCC callbacks, and both readbacks matched their expected content.
- Cleanup/O(1): PASS for this slice. The passing artifact recorded `post_cleanup_active_leases=0`, `processes_after_cleanup.count=0`, `connected_routes_after_cleanup=[]`, `final_orphans=0`, `final_missing=0`, `post_cleanup_orphans=0`, and `post_cleanup_missing=0`. Direct readback resource fields stayed at zero workspace/upperdir/run-dir tree bytes.
- Latency: PASS for the concurrency assertion. `fast-second` client elapsed was about `0.005s`; delayed `slow-first` was about `0.361s`. Concurrent callback OCC apply timings were about `0.00023s` and `0.00039s`.

Next iteration entry point:
- Resume the broader `backend/src/test_runner/tests/mock` migration suite. Known non-sandbox assertion drifts from the interrupted broad lane remain: public command-tool expectations should stay on `exec_command`, and old `<iteration_goal>` planner-context expectations should move to current `<goal>` semantics.

## Iteration 5 - 2026-06-02 13:23:00 +0800 CST

Updated scope:
- Mid-flight user correction: the public `backend/src/tools/sandbox/shell`
  package must be removed and replaced by `backend/src/tools/sandbox/exec_command`.
- The public `backend/src/tools/background` package must also be removed.
  Background is now typed-only for `exec_command(tty=true)`, `run_subagent`,
  and `delegate_workflow`.

Findings and issues:
- The previous compatibility path added a hidden generic background dispatch
  key for `shell`. That conflicts with the corrected contract and had to be
  reverted instead of retargeted.
- Mock background probes used stable test `background_task_id` values and
  generic `check_background_task_result` / `cancel_background_task` turns.
  Under the typed model those IDs must map to PTY session IDs and use
  `check_pty_command_progress` / `cancel_pty_command`.
- `exec_command` exposed the newer command output shape but did not carry the
  shell-era guarded-operation fields that migration probes still assert
  (`changed_paths`, `changed_path_kinds`, `mutation_source`,
  `conflict_reason`).

Fixes applied:
- Deleted the tracked `tools.sandbox.shell` and `tools.background` packages
  and removed leftover ignored cache directories from those paths.
- Removed the generic background compatibility branch from engine streaming,
  dispatch, and agent registry finalization. `run_subagent` remains the only
  engine-background-dispatched tool; PTY command and workflow background state
  remain typed through their own controls.
- Updated sandbox registry, prompt constants, schema/tool tests, request
  assertions, and mock probe imports so public command calls use
  `exec_command`.
- Updated the mock queue bridge so probe-requested stable background IDs launch
  `exec_command(tty=true)`, map to returned `pty_session_id`, poll with
  `check_pty_command_progress`, and cancel with `cancel_pty_command`.
- Extended `ExecCommandResult` / `command_tool_result` to preserve command
  stdout/stderr plus guarded-operation metadata needed by the migration probes.

Verification pending:
- Run focused lint and unit tests for the touched engine/tool/probe paths.
- Re-run focused live background/command scenarios, then resume the broader
  mock migration suite.

## Iteration 5 continuation - 2026-06-02 13:49:44 +0800 CST

Checkout summary:
- Current `HEAD`: `56ca1b668 refactor(tools): retire shell background tool surface`.
- Additional local edits in this continuation: `backend/tests/contracts/test_tool_intent_drift.py`, `docs/class_inventory/README.md`, and `docs/class_inventory/tools.md`.

Coverage gaps found:
- The daemon workspace route table still names the internal route verb `shell`, while the public decorated tool is now `exec_command`. The drift contract was still requiring a deleted `@tool(name="shell")`.
- `docs/class_inventory/tools.md` still advertised deleted `tools/background/*` and `tools/sandbox/shell/shell.py` classes.

Fixes applied:
- Added an explicit daemon-route to public-tool alias in `test_tool_intent_drift.py`: daemon verb `shell` is checked against public tool `exec_command` with the same `WRITE_ALLOWED` intent.
- Trimmed the tools class inventory to remove deleted background/shell classes and added the replacement `exec_command` / PTY command input/output schemas.

Commands run:
- `uv run ruff check backend/src/engine/background backend/src/engine/agent/factory.py backend/src/engine/tool_call/dispatch.py backend/src/engine/tool_call/streaming.py backend/src/tools/sandbox backend/src/tools/_names.py backend/src/test_runner/agent/mock backend/tests/unit_test/test_tools/test_sandbox_toolkit backend/tests/unit_test/test_engine/test_background_tasks.py backend/tests/unit_test/test_engine/test_spawn_agent.py backend/tests/unit_test/test_engine/test_provider_history.py backend/tests/unit_test/test_test_runner/test_probe_bridge.py backend/tests/contracts/test_tool_intent_drift.py`
  - Result: passed.
- `uv run pytest -q backend/tests/unit_test/test_tools/test_sandbox_toolkit backend/tests/unit_test/test_engine/test_background_tasks.py backend/tests/unit_test/test_engine/test_spawn_agent.py backend/tests/unit_test/test_engine/test_provider_history.py backend/tests/unit_test/test_test_runner/test_probe_bridge.py backend/tests/contracts/test_tool_intent_drift.py --tb=short`
  - First result: failed once in `test_tool_intent_matches_daemon_handlers_table[shell-write_allowed]`.
  - Fix: map daemon verb `shell` to public `exec_command` in the contract.
- `uv run ruff check backend/tests/contracts/test_tool_intent_drift.py`
  - Result: passed.
- `uv run pytest -q backend/tests/unit_test/test_tools/test_sandbox_toolkit backend/tests/unit_test/test_engine/test_background_tasks.py backend/tests/unit_test/test_engine/test_spawn_agent.py backend/tests/unit_test/test_engine/test_provider_history.py backend/tests/unit_test/test_test_runner/test_probe_bridge.py backend/tests/contracts/test_tool_intent_drift.py --tb=short`
  - Result: passed with `139 passed`.
- `git diff --check`
  - Result: passed.

Fresh artifacts inspected:
- No `.sweevo_runs` or live sandbox artifacts were produced in this continuation.
- Verified the current source tree has no tracked files under `backend/src/tools/background` or `backend/src/tools/sandbox/shell`.

Current verdict:
- Correctness: PASS for the focused unit/contract slice covering sandbox toolkit, background supervisor/subagent controls, probe bridge PTY mapping, spawn-agent registry synthesis, provider-history background reduction, and tool-intent drift.
- O(1) memory/disk: not re-measured in this continuation because no live sandbox run was executed.
- Latency: not re-measured in this continuation because no live sandbox run was executed.

Next iteration entry point:
- Run focused mock background command scenarios and then resume the broader `backend/src/test_runner/tests/mock` migration suite, still skipping `backend/src/test_runner/tests/real_agent` and real-LLM tests.

## Iteration 6 - 2026-06-02 13:59:16 +0800 CST

Checkout summary:
- Current `HEAD`: `56ca1b668 refactor(tools): retire shell background tool surface`.
- Additional local edits before this iteration: Iteration 5 contract/class-inventory edits plus PTY completion handoff fixes in `backend/src/engine/background/task_supervisor.py`, `backend/src/tools/sandbox/_lib/pty_command_tool.py`, and typed PTY control tools.

Plan path and target files:
- Plan: `docs/plans/test_runner_migration_PLAN.md`.
- Focus target: `backend/src/test_runner/tests/mock/sandbox/background_tool/test_background_shell_golden.py::test_background_shell_golden`.
- Supporting code touched: `backend/src/test_runner/agent/mock/probe_bridge.py` behavior from prior iteration, PTY control tools, and `BackgroundTaskSupervisor`.

Coverage gaps found:
- No new coverage gap before the live run; this iteration is a correctness fix for the focused background-command golden scenario.

Commands run:
- `uv run pytest --collect-only -q backend/src/test_runner/tests/mock/sandbox/background_tool`
  - Result: passed collection with 14 tests.
- `env EOS_SANDBOX_PROVIDER=docker EOS_SANDBOX_RUNTIME=rust EOS_LIVE_E2E_IMAGE=sweevo-dask__dask-10042:latest uv run pytest -q backend/src/test_runner/tests/mock/sandbox/background_tool/test_background_shell_golden.py::test_background_shell_golden --tb=short --durations=10`
  - First result: failed after a live run.
- `uv run ruff check backend/src/engine/background/task_supervisor.py backend/src/tools/sandbox/_lib/pty_command_tool.py backend/src/tools/sandbox/check_pty_command_progress backend/src/tools/sandbox/write_pty_command_stdin backend/src/tools/sandbox/cancel_pty_command backend/tests/unit_test/test_tools/test_command_result_output.py`
  - Result: passed.
- `uv run pytest -q backend/tests/unit_test/test_tools/test_command_result_output.py backend/tests/unit_test/test_engine/test_background_task_emitters.py backend/tests/unit_test/test_tools/test_sandbox_toolkit --tb=short`
  - First result: failed because the new supervisor-backed unit test registered a PTY outside a running event loop.
  - Fix: made the unit test async.
- `uv run pytest -q backend/tests/unit_test/test_tools/test_command_result_output.py backend/tests/unit_test/test_engine/test_background_task_emitters.py backend/tests/unit_test/test_tools/test_sandbox_toolkit --tb=short`
  - Result: passed with `93 passed`.

Fresh artifacts inspected:
- `.sweevo_runs/scenario_logs/sandbox.background_shell_golden/20260602T055218Z_3983ec5bcb41/run.json`
- `.sweevo_runs/scenario_logs/sandbox.background_shell_golden/20260602T055218Z_3983ec5bcb41/sandbox_events.jsonl`
- `.sweevo_runs/scenario_logs/sandbox.background_shell_golden/20260602T055218Z_3983ec5bcb41/workflow_01_c58f194c-c32a-4af3-bd66-1752516adc90/.../02_executor_f99d5b93-aa56-4c4f-8d36-fe1cb4bf2253:gen:background_shell_golden/message.jsonl`

First failure/stop signal:
- `test_background_shell_golden` observed `report.request_status == "failed"` because the executor failed with `exec_command failed: {"status": "error", ... "stderr": "pty_session_not_found"}` while polling `check_pty_command_progress`.

Root-cause hypothesis and evidence:
- `sandbox_events.jsonl` showed the launch path was correct: `api.v1.exec_command` registered `pty_4`, `pty_5`, and `pty_6` under one Rust daemon boot epoch and progress calls initially returned `running`.
- The failure appeared after one sibling PTY returned `ok`: `api.v1.pty.collect_completed` had already consumed terminal completions for the other PTYs, so later `api.v1.pty.progress` calls for `pty_5` and `pty_6` returned `pty_session_not_found`.
- This is a PTY completion ownership race between engine-side background notification polling and model-facing typed PTY controls, not LSP cold start, daemon restart, or serialized plugin/tool execution.

Fixes applied:
- Added `BackgroundTaskSupervisor.get_pty_command_result()` so typed controls can recover a terminal result already claimed by notification polling.
- Added `recover_pty_result_from_supervisor()` in `tools.sandbox._lib.pty_command_tool`.
- Wired recovery into `check_pty_command_progress`, `write_pty_command_stdin`, and `cancel_pty_command`.
- Added a unit test for recovering the stored terminal PTY result when the daemon control call reports `pty_session_not_found`.

Current verdict:
- Correctness: PASS for the focused PTY handoff unit slice; live rerun pending.
- O(1) memory/disk: not re-measured yet in this iteration because the post-fix live rerun is pending.
- Latency: not re-measured yet in this iteration because the post-fix live rerun is pending.

Next iteration entry point:
- Rerun the focused Docker live `test_background_shell_golden` and inspect the new `.sweevo_runs` artifact before expanding to the rest of `backend/src/test_runner/tests/mock/sandbox/background_tool`.

### Iteration 6 continuation - 2026-06-02 14:02:17 +0800 CST

Post-fix command run:
- `env EOS_SANDBOX_PROVIDER=docker EOS_SANDBOX_RUNTIME=rust EOS_LIVE_E2E_IMAGE=sweevo-dask__dask-10042:latest uv run pytest -q backend/src/test_runner/tests/mock/sandbox/background_tool/test_background_shell_golden.py::test_background_shell_golden --tb=short --durations=10`
  - Result: passed with `1 passed in 25.39s`.
  - Pytest durations: setup `17.99s`, call `7.31s`.

Fresh artifacts inspected:
- `.sweevo_runs/scenario_logs/sandbox.background_shell_golden/20260602T060007Z_097133a30c5f/run.json`
- `.sweevo_runs/scenario_logs/sandbox.background_shell_golden/20260602T060007Z_097133a30c5f/sandbox_events.jsonl`
- `.sweevo_runs/scenario_logs/sandbox.background_shell_golden/20260602T060007Z_097133a30c5f/metrics.json`
- `.sweevo_runs/scenario_logs/sandbox.background_shell_golden/20260602T060007Z_097133a30c5f/performance_report.json`
- `.sweevo_runs/scenario_logs/sandbox.background_shell_golden/20260602T060007Z_097133a30c5f/performance_report.md`

Artifact findings:
- `run.json`: status `finished`.
- Workflow/attempt artifacts: generator status `done`, reducer status `done`, iteration status `succeeded`.
- `metrics.json`: `tool_calls_total=60`, `tool_errors_total=0`.
- `sandbox_events.jsonl`: all three PTYs (`pty_1`, `pty_2`, `pty_3`) progressed from `running` to `ok`; no `pty_session_not_found` in the fresh run.
- Daemon audit pull: `events_pulled=367`, `dropped_event_count=0`, `daemon_restarts_observed=0`.

Performance/resource result:
- Daemon API totals from `performance_report.json`:
  - `api.v1.exec_command`: 4 calls, p50 `59.9ms`, p95/max `86.0ms`.
  - `api.v1.pty.progress`: 40 calls, p50 `0.13ms`, p95 `0.29ms`, max `0.48ms`.
  - `api.v1.pty.collect_completed`: 42 calls, p50 `0.11ms`, p95 `0.19ms`, max `0.69ms`.
  - `api.v1.write_file`: 1 call, max `8.85ms`.
- Resource maxima from fresh events: `resource.command_exec.workspace_tree_exists=0`, `workspace_tree_bytes=0`, `run_dir_tree_exists=0`, `run_dir_tree_bytes=0`, `upperdir_tree_bytes=0`; max manifest depth observed `2`.
- Performance report summary: peak `upperdir_bytes_total=0`, peak `layer_count=1`, warnings `(none)`.

Current verdict:
- Correctness: PASS for the focused live background-command golden scenario.
- O(1) memory/disk: PASS for this focused scenario; no workspace/run-dir/upperdir tree growth was observed for command resources, and artifact inventory stayed bounded.
- Latency: PASS for this focused scenario; PTY progress/collection stayed sub-millisecond p95, and finite command p95/max was `86.0ms`.

Next iteration entry point:
- Run the full `backend/src/test_runner/tests/mock/sandbox/background_tool` folder under Docker/Rust and inspect the newest artifacts before broadening to the rest of `backend/src/test_runner/tests/mock`.

## Iteration 7 - 2026-06-02 14:19:26 +0800 CST

Checkout summary:
- Current `HEAD`: `56ca1b668 refactor(tools): retire shell background tool surface`.
- `backend/src/tools/background` and `backend/src/tools/sandbox/shell` are absent in the live checkout.
- Local edits entering this iteration included the Iteration 6 PTY terminal-result recovery and the class-inventory/tool-intent cleanup.

Plan path and target files:
- Plan: `docs/plans/test_runner_migration_PLAN.md`.
- Focus target: `backend/src/test_runner/tests/mock/sandbox/background_tool`.
- Supporting code touched: `backend/src/test_runner/agent/mock/probe_bridge.py`, `backend/src/test_runner/agent/mock/background_shell_probe.py`, and selected background-command tests.

Commands run:
- `env EOS_SANDBOX_PROVIDER=docker EOS_SANDBOX_RUNTIME=rust EOS_LIVE_E2E_IMAGE=sweevo-dask__dask-10042:latest uv run pytest -q backend/src/test_runner/tests/mock/sandbox/background_tool --tb=short --durations=20`
  - Result: produced failures and then became CPU-bound in report/provider-history preparation after `sandbox.background_shell_exhaustion`; the process was terminated to inspect the first actionable failure from the generated artifacts.
- `env EOS_SANDBOX_PROVIDER=docker EOS_SANDBOX_RUNTIME=rust EOS_LIVE_E2E_IMAGE=sweevo-dask__dask-10042:latest uv run pytest -q backend/src/test_runner/tests/mock/sandbox/background_tool/test_background_engine_restart_no_lease_leak.py::test_background_engine_restart_no_lease_leak --tb=short --durations=10`
  - First result: failed with `assert summary["inflight_during_launch"] >= 1`, where the summary value was `0`.
- `uv run ruff check backend/src/test_runner/agent/mock/probe_bridge.py backend/src/test_runner/agent/mock/background_shell_probe.py backend/src/test_runner/tests/mock/sandbox/background_tool/test_background_engine_restart_no_lease_leak.py backend/src/test_runner/tests/mock/sandbox/background_tool/test_background_heartbeat_loss_reaps_only_stale_bg.py backend/src/test_runner/tests/mock/sandbox/background_tool/test_background_exit_iws_drains_agent_tasks.py backend/src/test_runner/tests/mock/sandbox/background_tool/test_background_many_small_writes_do_not_starve_dispatcher.py backend/src/test_runner/scenarios/sandbox/background_shell.py`
  - Result: passed.
- `uv run pytest -q backend/tests/unit_test/test_test_runner/test_probe_bridge.py backend/tests/unit_test/test_tools/test_command_result_output.py --tb=short`
  - Result: passed with `4 passed`.

Fresh artifacts inspected:
- `.sweevo_runs/scenario_logs/sandbox.background_engine_restart_no_lease_leak/20260602T061011Z_8550576f1bdd/run.json`
- `.sweevo_runs/scenario_logs/sandbox.background_engine_restart_no_lease_leak/20260602T061011Z_8550576f1bdd/metrics.json`
- `.sweevo_runs/scenario_logs/sandbox.background_engine_restart_no_lease_leak/20260602T061011Z_8550576f1bdd/sandbox_events.jsonl`
- `.sweevo_runs/scenario_logs/sandbox.background_engine_restart_no_lease_leak/20260602T061011Z_8550576f1bdd/performance_report.json`

First failure/stop signal:
- The isolated engine-restart test failed because the probe measured `api.v1.inflight_count` for an `exec_command(tty=true)` launch.
- The artifact showed the PTY was alive and progressing: `api.v1.exec_command` returned `running`, `background_tool.started` recorded `pty_1`, and repeated `api.v1.pty.progress` calls returned `running` before terminal `ok`.

Root-cause hypothesis and evidence:
- After the migration, a background command is no longer a long-running daemon RPC invocation. `exec_command(tty=true)` returns quickly and leaves daemon-owned work in the PTY registry.
- `api.v1.inflight_count` correctly returned zero because it counts background RPC invocations, not live PTY sessions.
- The mock bridge still injected hidden `_sandbox_invocation_id` / `_disable_sandbox_heartbeat` controls for PTY launches; that was stale generic-background compatibility and had no useful contract with typed PTY sessions.

Fixes applied:
- Removed hidden invocation-control injection from `backend/src/test_runner/agent/mock/probe_bridge.py` for PTY launches.
- Switched background-command probes from `inflight_count` diagnostics to `pty_session_count` diagnostics.
- Reworked the heartbeat-loss probe into the post-migration typed behavior: one PTY session completes and publishes, one PTY session is cancelled and does not publish, and a foreground command still runs during recovery.
- Reworked the engine-abandon probe to cancel the PTY-backed bridge task at the abandonment point instead of waiting for nonexistent invocation TTL cleanup.
- Updated tests to assert `pty_sessions_during_launch`, `pty_sessions_after`, and `default_pty_sessions`.
- Updated scenario prose so the historical heartbeat-loss scenario no longer claims explicit invocation ids or daemon TTL reaping for PTY-backed command sessions.

Current verdict:
- Correctness: PASS for the focused unit/static slice after the PTY-session contract fix; live rerun pending.
- O(1) memory/disk: not re-measured after the fix yet.
- Latency: not re-measured after the fix yet.

Next iteration entry point:
- Rerun `test_background_engine_restart_no_lease_leak` and `test_background_heartbeat_loss_reaps_only_stale_bg` under Docker/Rust, inspect the fresh artifacts, then retry the full `backend/src/test_runner/tests/mock/sandbox/background_tool` folder.

### Iteration 7 continuation - 2026-06-02 14:23:24 +0800 CST

Post-fix commands run:
- `env EOS_SANDBOX_PROVIDER=docker EOS_SANDBOX_RUNTIME=rust EOS_LIVE_E2E_IMAGE=sweevo-dask__dask-10042:latest uv run pytest -q backend/src/test_runner/tests/mock/sandbox/background_tool/test_background_engine_restart_no_lease_leak.py::test_background_engine_restart_no_lease_leak backend/src/test_runner/tests/mock/sandbox/background_tool/test_background_heartbeat_loss_reaps_only_stale_bg.py::test_background_heartbeat_loss_reaps_only_stale_bg --tb=short --durations=10`
  - First post-fix result: both scenario assertions passed, but both tests failed in `assert_background_performance_artifacts()` because it still required deleted shell timing keys: `command_exec.mount_workspace_s`, `command_exec.run_command_s`, `command_exec.capture_upperdir_s`, and `api.shell.total_s`.
- `uv run ruff check backend/src/agents/profile/main/root.md backend/src/agents/profile/main/executor.md backend/src/agents/profile/main/reducer.md backend/src/test_runner/tests/mock/sandbox/background_tool/_background_shell_invariants.py backend/src/test_runner/agent/mock/probe_bridge.py backend/src/test_runner/agent/mock/background_shell_probe.py backend/src/test_runner/tests/mock/sandbox/background_tool/test_background_engine_restart_no_lease_leak.py backend/src/test_runner/tests/mock/sandbox/background_tool/test_background_heartbeat_loss_reaps_only_stale_bg.py backend/src/test_runner/tests/mock/sandbox/background_tool/test_background_exit_iws_drains_agent_tasks.py backend/src/test_runner/tests/mock/sandbox/background_tool/test_background_many_small_writes_do_not_starve_dispatcher.py backend/src/test_runner/scenarios/sandbox/background_shell.py`
  - Result: passed.
- Repeated the same two live tests under Docker/Rust.
  - Result: passed with `2 passed in 36.61s`.
  - Pytest durations: engine-restart setup `17.07s`, engine-restart call `5.26s`, heartbeat setup `7.96s`, heartbeat call `6.19s`.

Additional fixes applied:
- Removed stale `shell` entries from `backend/src/agents/profile/main/root.md`, `executor.md`, and `reducer.md`; reducer now requests `exec_command`, `check_pty_command_progress`, and `cancel_pty_command`.
- Replaced the background-command artifact helper's old shell timing-key requirement with current tool-metric presence checks for `exec_command` and `check_pty_command_progress`.

Fresh artifacts inspected:
- `.sweevo_runs/scenario_logs/sandbox.background_engine_restart_no_lease_leak/20260602T062243Z_80881019cc89/run.json`
- `.sweevo_runs/scenario_logs/sandbox.background_engine_restart_no_lease_leak/20260602T062243Z_80881019cc89/metrics.json`
- `.sweevo_runs/scenario_logs/sandbox.background_engine_restart_no_lease_leak/20260602T062243Z_80881019cc89/sandbox_events.jsonl`
- `.sweevo_runs/scenario_logs/sandbox.background_engine_restart_no_lease_leak/20260602T062243Z_80881019cc89/performance_report.json`
- `.sweevo_runs/scenario_logs/sandbox.background_heartbeat_loss_reaps_only_stale_bg/20260602T062256Z_f74534216465/run.json`
- `.sweevo_runs/scenario_logs/sandbox.background_heartbeat_loss_reaps_only_stale_bg/20260602T062256Z_f74534216465/metrics.json`
- `.sweevo_runs/scenario_logs/sandbox.background_heartbeat_loss_reaps_only_stale_bg/20260602T062256Z_f74534216465/sandbox_events.jsonl`
- `.sweevo_runs/scenario_logs/sandbox.background_heartbeat_loss_reaps_only_stale_bg/20260602T062256Z_f74534216465/performance_report.json`

Artifact findings:
- Both `run.json` files reported status `finished`.
- Engine-restart metrics: `tool_calls_total=29`, `tool_errors_total=1`; the one tool error is the expected negative `read_file` check for the cancelled/non-published path.
- Heartbeat-loss metrics: `tool_calls_total=46`, `tool_errors_total=1`; the one tool error is the expected negative `read_file` check for the cancelled/non-published stale path.
- Tool metrics present:
  - Engine-restart: `exec_command` count `3`, `check_pty_command_progress` count `8`, `cancel_pty_command` count `1`.
  - Heartbeat-loss: `exec_command` count `4`, `check_pty_command_progress` count `24`, `cancel_pty_command` count `1`.
- PTY audit events:
  - Engine-restart: one `background_tool.started`, eight `background_tool.progress`, one `background_tool.cancelled`.
  - Heartbeat-loss: two `background_tool.started`, twenty-four `background_tool.progress`, one `background_tool.cancelled`; one PTY reached `ok`, the stale PTY was cancelled.

Performance/resource result:
- Engine-restart p95 tool latency: `exec_command=0.061ms`, `check_pty_command_progress=0.179ms`, `cancel_pty_command=0.162ms`.
- Heartbeat-loss p95 tool latency: `exec_command=0.115ms`, `check_pty_command_progress=0.104ms`, `cancel_pty_command=0.051ms`.
- Both fresh reports had no warnings.
- For both fresh reports, command resource max values were bounded at zero for `resource.command_exec.workspace_tree_bytes`, `workspace_tree_exists`, `run_dir_tree_bytes`, and `upperdir_tree_bytes`.

Current verdict:
- Correctness: PASS for the two affected live scenarios and for the focused static/unit slice.
- O(1) memory/disk: PASS for these two scenarios; command workspace/run-dir/upperdir tree bytes stayed zero.
- Latency: PASS for these two scenarios; typed command and PTY-control tool p95s stayed sub-millisecond.

Next iteration entry point:
- Rerun the full `backend/src/test_runner/tests/mock/sandbox/background_tool` folder under Docker/Rust.
