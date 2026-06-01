# Phase 3T PTY Command Iteration Report

## Iteration 1 - 2026-06-01 11:39 CST

- Checkout: `565f4ea22` with the Phase 3T implementation changes in the worktree.
- Plan path: `docs/plans/sandbox-rust-external-migration-PHASE-3T-PTY-COMMAND-DESIGN.md`.
- Scope: Docker-only live E2E, load, and p95 performance gates. Daytona is intentionally skipped.
- Current implementation evidence before live run:
  - `cargo test -p eos-protocol -p eos-runner -p eos-daemon`: passed.
  - `cargo check -p eos-daemon --target x86_64-unknown-linux-musl`: passed.
  - focused Python pytest/ruff/tool-registry checks: passed.
- Setup evidence:
  - Docker CLI is available via `/usr/local/bin/docker`.
  - Docker server is Linux/arm64 through Docker Desktop.
  - Local images include `sweevo-dask__dask-10042:latest` and `xingyaoww/sweb.eval.x86_64.dask_s_dask-10042:latest`.
  - Local artifacts exist at `sandbox/dist/eosd-linux-amd64` and `sandbox/dist/eosd-linux-arm64`.
- First gap found:
  - `backend/scripts/bench_rust_daemon_phase3.py` is useful for Docker setup, artifact upload, LayerStack seeding, daemon startup, and load/report patterns, but it measures the older `api.v1.shell` raw-argv CP-4 surface. Phase 3T closeout requires fresh `api.v1.exec_command` and PTY-control measurements.
- Next entry point:
  - Run Docker artifact verification, then run or add a Phase 3T-specific Docker benchmark path for finite command, PTY start/progress/write/cancel, load matrix, and p95 gates.

## Iteration 2 - 2026-06-01 11:52 CST

### Runtime Artifact

- Built and uploaded fresh Linux amd64 `eosd` for Docker live checks.
- Report: `bench/local-eosd-amd64-phase3t-20260601.json`.
- Artifact: `sandbox/dist/eosd-linux-amd64`.
- SHA-256:
  `0f540967c790787e0076c6cbbf624c54c05a66f026e1e8ba0fca1fdca70972d5`.
- Upload gate: passed; remote mode `0o755`; `eosd --version` returned
  `eosd 0.1.0`.

### Full Tiered Docker Live E2E

- Command family:
  `backend.tests.live_e2e_test._tools.run_tiered --provider docker --tier 0,1,2,3,4,5,6 --run-id phase3t-docker-20260601-rust-control-op-blocker`.
- Environment:
  `EOS_SANDBOX_PROVIDER=docker`, `EOS_SANDBOX_RUNTIME=rust`,
  `EOS_LIVE_E2E_IMAGE=sweevo-dask__dask-10042:latest`,
  `EOS_DOCKER_PRIVILEGED=1`.
- Latest summary artifact:
  `.omc/results/progressive-test-summary-phase3t-docker-20260601-rust-control-op-blocker.jsonl`.
- Tier result:
  - Tier 0 preflight: passed in 0.711 s.
  - Tier 1 smoke: failed in 8.260 s.
  - Tiers 2-6: skipped by cascade.
- Direct Tier 1 smoke rerun after the Rust runtime upload fix reached the Rust
  daemon and failed on:
  `unknown_op: unknown op: api.ensure_workspace_base`.
- Verdict: full tiered Docker live E2E is blocked by a Rust daemon control-plane
  coverage gap, not by the Phase 3T PTY command surface. The live suite requires
  layer-stack workspace-base setup before it can exercise the later tiers under
  `EOS_SANDBOX_RUNTIME=rust`.

### Phase 3T Docker PTY/Load/P95 Gate

- Added dedicated harness:
  `backend/scripts/bench_rust_daemon_phase3t_pty.py`.
- Strict report:
  `bench/phase3t-pty-command-docker-20260601-strict.json`.
- Top-level gate: passed.
- Correctness checks: passed.
  - stdout/stderr split.
  - command environment resolves `python` to
    `/opt/miniconda3/envs/testbed/bin/python`.
  - finite command writes publish through OCC and are readable.
  - finite `nohup ... &` descendant cleanup leaves no matching process.
- P95 gates:
  - finite `exec_command(tty=false, cmd=true)`: 49.733 ms, gate <= 60 ms.
  - `exec_command(tty=true, cmd=true)`: 49.633 ms, gate <= 100 ms.
  - `check_pty_command_progress`: 1.273 ms, gate <= 20 ms.
  - `write_pty_command_stdin` to visible echo: 57.289 ms, gate <= 100 ms.
  - `cancel_pty_command`: 55.426 ms, gate <= 500 ms.
  - cancel cleanup: 414.419 ms, gate <= 2500 ms.
- Load matrix: passed at 1/3/5/10 concurrency for finite no-op, finite write,
  and PTY no-op operations. Max observed p95 among the load cells was 167.020 ms
  for 10-way finite writes; all samples succeeded.

### Notes

- Daytona was not run.
- The first Phase 3T benchmark report,
  `bench/phase3t-pty-command-docker-20260601.json`, exposed that a
  150 ms post-write yield made the 100 ms write gate impossible.
- The second report,
  `bench/phase3t-pty-command-docker-20260601-rerun.json`, exposed a stricter
  harness issue: `pty_write_echo` p95 was under the target, but sample-level
  echo correctness was not included in the top-level gate.
- The strict harness now counts operation sample correctness in
  `operation_samples_ok` and measures PTY write latency separately from the
  follow-up progress poll that proves the child consumed stdin.

### Remaining Blocker

To make the full tiered Docker live E2E pass under `EOS_SANDBOX_RUNTIME=rust`,
the Rust daemon needs the layer-stack workspace setup control op first observed
as missing:

```text
api.ensure_workspace_base
```

The Python daemon implements this live-suite setup path today. Porting it is
outside the PTY command gate itself, but it is required before tiers 1-6 can be
used as full Rust-runtime live evidence.

### Focused Verification After Report Update

- `.venv/bin/python -m ruff check backend/scripts/bench_rust_daemon_phase3t_pty.py backend/src/sandbox/host/runtime_bundle.py backend/src/sandbox/host/runtime_artifact/__init__.py backend/tests/unit_test/test_sandbox/test_daemon/test_bundle_upload.py`:
  passed.
- `.venv/bin/python -m pytest backend/tests/unit_test/test_sandbox/test_daemon/test_bundle.py backend/tests/unit_test/test_sandbox/test_daemon/test_bundle_upload.py -q`:
  16 passed.
- `git diff --check`: passed.
- `cargo check -p eos-daemon --target x86_64-unknown-linux-musl`: passed with
  pre-existing warnings in adjacent crates.

## Iteration 3 - 2026-06-01 14:15 CST

### Implementation Delta

- Implemented Rust workspace-base control ops:
  `api.ensure_workspace_base`, `api.build_workspace_base`, and
  `api.workspace_binding`.
- Moved Docker live LayerStack storage to the existing provider scratch tmpfs:
  `/eos-mount-scratch/eos-sandbox-runtime/layer-stack`.
- Fixed Rust and Python delete-layer whiteouts to prefer kernel overlay
  whiteout device nodes and retain xattr/logical fallbacks. This resolved the
  Python-vs-Rust mismatch where lower xattr whiteouts hid lookup but leaked
  placeholder names through `readdir`/`os.walk`.
- Preserved symlinks in Rust and Python workspace-base imports.
- Restored Rust OCC route/timing parity:
  `occ.commit.gated_path_count`, `occ.commit.direct_path_count`, and
  transaction-scoped `occ.commit.total_s` now come from the commit path, not the
  outer queue wait.
- Optimized gated validation by reading the active manifest once per transaction
  and caching fresh parent-directory absence for new-file workloads.
- Fixed PTY natural-exit cleanup so `tty=true` commands that spawn
  `nohup ... 2>&1 &` descendants do not leave the descendant alive after the
  PTY runner exits.

### Runtime Artifact

- Final Docker-tested amd64 artifact: `sandbox/dist/eosd-linux-amd64`.
- SHA-256:
  `71f6533c2d41861303cc7fef4828738cd16e352c539b59c67e489987f1a36162`.
- Upload report:
  `bench/local-eosd-amd64-phase3t-pty-cleanup-conditional-20260601.json`.
- Upload gate: passed; remote mode `0o755`; `eosd --version` returned
  `eosd 0.1.0`.

### Full Tiered Docker Live E2E

- Command family:
  `backend.tests.live_e2e_test._tools.run_tiered --provider docker --tier 0,1,2,3,4,5,6 --run-id phase3t-rust-scratch-full-final-20260601`.
- Environment:
  `EOS_SANDBOX_PROVIDER=docker`, `EOS_SANDBOX_RUNTIME=rust`,
  `EOS_LIVE_E2E_IMAGE=sweevo-dask__dask-10042:latest`,
  `EOS_DOCKER_PRIVILEGED=1`.
- Summary artifact:
  `.omc/results/progressive-test-summary-phase3t-rust-scratch-full-final-20260601.jsonl`.
- Tier result:
  - Tier 0 preflight: passed in 0.73 s.
  - Tier 1 smoke: passed in 12.70 s.
  - Tier 2 k-scaling spot check: passed in 12.86 s.
  - Tier 3 single-axis matrices: passed in 33.72 s.
  - Tier 4 cross-axis matrices: passed in 48.76 s.
  - Tier 5 soak: passed in 23.04 s.
  - Tier 6 adversarial: passed in 13.49 s.

### Phase 3T Docker PTY/Load/P95 Gate

- Report:
  `bench/phase3t-pty-command-docker-20260601-pty-cleanup-conditional.json`.
- Top-level gate: passed.
- Correctness checks: passed.
  - stdout/stderr split.
  - command environment resolves `python` to
    `/opt/miniconda3/envs/testbed/bin/python`.
  - finite command writes publish through OCC and are readable.
  - finite `tty=false` `nohup ... 2>&1 &` descendant cleanup leaves no matching
    process.
  - PTY `tty=true` `nohup ... 2>&1 &` descendant cleanup leaves no matching
    process.
- P95 gates:
  - finite `exec_command(tty=false, cmd=true)`: 46.393 ms, gate <= 60 ms.
  - `exec_command(tty=true, cmd=true)`: 48.933 ms, gate <= 100 ms.
  - `check_pty_command_progress`: 1.688 ms, gate <= 20 ms.
  - `write_pty_command_stdin` to visible echo: 57.441 ms, gate <= 100 ms.
  - `cancel_pty_command`: 54.822 ms, gate <= 500 ms.
  - cancel cleanup: 403.108 ms, gate <= 2500 ms.
- Load matrix: passed at 1/3/5/10 concurrency for finite no-op, finite write,
  and PTY no-op operations.

### Final Local Verification

- `cargo fmt --all --check && cargo test -p eos-layerstack -p eos-daemon -p eos-overlay && cargo check -p eos-daemon --target x86_64-unknown-linux-musl`:
  passed with pre-existing warnings in adjacent crates.
- `.venv/bin/python -m ruff check backend/src/sandbox/daemon/paths.py backend/scripts/bench_rust_daemon_phase2.py backend/scripts/bench_rust_daemon_phase3.py backend/scripts/bench_rust_daemon_phase3t_pty.py backend/src/sandbox/layer_stack/changes.py backend/src/sandbox/layer_stack/layer_index.py backend/src/sandbox/layer_stack/view.py backend/tests/live_e2e_test/sandbox/workspace_base/test_base_import_cost.py backend/src/sandbox/host/runtime_artifact/__init__.py`:
  passed.
- `.venv/bin/python -m pytest -q backend/tests/unit_test/test_sandbox/test_layer_stack/test_workspace_base.py`:
  5 passed.

### Notes

- Daytona was not run.
- Live runs used the existing Docker setup: `EOS_DOCKER_PRIVILEGED=1`, the
  existing `sweevo-dask__dask-10042:latest` image, and the repo tier/bench
  scripts.
- Rust daemon isolated-workspace public ops are still not registered in this
  phase, so the final Rust-runtime comparison is between shared ephemeral
  command paths. Existing Python isolated-workspace tests remain separate live
  coverage for Python daemon isolated mode.

## 2026-06-01 `/eos` Path Follow-Up and PTY Notification Closeout

### Runtime Path and Upload Fixes

- Unified runtime paths are now exercised with `/eos/daemon`,
  `/eos/layer-stack`, and `/eos/mount`.
- Docker `put_archive` cannot be used directly against the tmpfs-mounted
  `/eos` tree on this setup: it can report success while files are not visible
  from container exec. The upload path now keeps the fast bulk transfer by
  staging with `put_archive` under `/tmp`, then using one `exec` copy/extract
  into `/eos/daemon` or `/eos/layer-stack`.
- Docker tmpfs for `/eos` now includes `exec`; without it,
  `/eos/daemon/eosd` fails with `Permission denied`.
- Rebuilt amd64 package:
  `sandbox/dist/eosd-linux-amd64`.
- Current amd64 SHA-256:
  `b9faf30b00e94b3322ccab4505ff24494c07f0f6d8ba14b986d75e01aa4d49ac`.

### Notification and Lifecycle Fixes

- PTY records now retain the command and completion result returned by the Rust
  daemon completion mailbox.
- The query loop drains PTY completions before the next provider request and
  emits one `[BACKGROUND COMPLETED]` system notification for natural exit or
  timeout.
- Explicit `cancel_pty_command` marks the PTY locally and suppresses duplicate
  spontaneous natural-exit notifications.
- `BackgroundTaskSupervisor.has_pending()` now includes running PTY sessions so
  query-loop cleanup and lifecycle gates do not miss PTY-only work.

### Focused Verification

- `uv run pytest backend/tests/unit_test/test_engine/test_background_task_emitters.py backend/tests/unit_test/test_sandbox/test_daemon/test_bundle_upload.py backend/tests/unit_test/test_sandbox/test_provider/test_docker_adapter.py -q`:
  46 passed.
- `uv run pytest backend/tests/unit_test/test_sandbox/test_isolated_workspace_lifecycle_background.py backend/tests/unit_test/test_tools/test_hooks/test_require_no_inflight_background_tasks.py -q`:
  20 passed.
- `ruff check` over the touched engine, tool, Docker provider, benchmark, and
  focused test files: passed.

### Full Tiered Docker Live E2E

- Command family:
  `backend.tests.live_e2e_test._tools.run_tiered --provider docker --tier 0,1,2,3,4,5,6 --run-id phase3t-current-eos-paths-post-notify-tier0-6-20260601`.
- Environment:
  `EOS_SANDBOX_PROVIDER=docker`, `EOS_SANDBOX_RUNTIME=rust`,
  `EOS_LIVE_E2E_IMAGE=sweevo-dask__dask-10042:latest`,
  `EOS_DOCKER_PRIVILEGED=1`.
- Summary artifact:
  `.omc/results/progressive-test-summary-phase3t-current-eos-paths-post-notify-tier0-6-20260601.jsonl`.
- Tier result:
  - Tier 0 preflight: passed in 0.80 s.
  - Tier 1 smoke: passed in 12.82 s.
  - Tier 2 k-scaling spot check: passed in 12.49 s.
  - Tier 3 single-axis matrices: passed in 31.59 s.
  - Tier 4 cross-axis matrices: passed in 47.39 s.
  - Tier 5 soak: passed in 22.68 s.
  - Tier 6 adversarial: passed in 13.44 s.

### Live Docker PTY/Load/P95 Gate

- Report:
  `bench/phase3t-pty-command-docker-20260601-current-eos-paths-post-notify.json`.
- Top-level gate: passed.
- Correctness checks: passed.
  - stdout/stderr split.
  - command environment resolves `python` to
    `/opt/miniconda3/envs/testbed/bin/python`.
  - finite command writes publish through OCC and are readable.
  - finite `tty=false` `nohup ... 2>&1 &` descendant cleanup leaves no matching
    process.
  - PTY `tty=true` `nohup ... 2>&1 &` descendant cleanup leaves no matching
    process.
- Operation p95s:
  - finite `exec_command(tty=false, cmd=true)`: 44.548 ms.
  - `exec_command(tty=true, cmd=true)`: 49.181 ms.
  - `check_pty_command_progress`: 1.861 ms.
  - `write_pty_command_stdin` to visible echo: 54.859 ms.
  - `cancel_pty_command`: 54.613 ms.
  - cancel cleanup: 387.709 ms.
- Load matrix: passed at 1/3/5/10 concurrency for finite no-op, finite write,
  and PTY no-op operations.

### Live PTY Notification Probe

- Natural PTY exit produced one notification:
  `[BACKGROUND COMPLETED] pty_session_id="pty_1" status=ok exit_code=0`.
- PTY timeout produced one notification:
  `[BACKGROUND COMPLETED] pty_session_id="pty_2" status=timed_out exit_code=124`.
- Explicit PTY cancel returned `status=cancelled` and produced no duplicate
  completion notification.

### Remaining Gap

- Rust daemon isolated-workspace public lifecycle ops are still not registered
  in the dispatcher (`api.isolated_workspace.enter/exit`), so Rust PTY behavior
  cannot yet be compared against isolated-workspace mode. The current Rust live
  evidence is for shared ephemeral workspace; focused Python/IWS gate tests
  confirm active PTY records block isolated lifecycle operations locally.

## 2026-06-01 Final Timeout/Cancel Fix Verification

This section supersedes the earlier 2026-06-01 hash and post-notify gate above.
The final fix keeps daemon-side external PTY cancellation working while making
PTY timeout kill the direct child promptly and letting the daemon finalizer clean
the process group.

### Final Runtime Artifact

- Package command:
  `cargo run -p xtask -- package --target x86_64-unknown-linux-musl --out-dir dist --builder rust-lld`.
- Packaged artifact:
  `sandbox/dist/eosd-linux-amd64`.
- amd64 SHA-256 pinned in `runtime_artifact`:
  `cb949fce52784b6f7634589a707f54f40f01f75051bc7259832bc2fee63c54bf`.
- Linux target check:
  `cargo check -p eosd --target x86_64-unknown-linux-musl` passed with
  pre-existing adjacent-crate warnings.

### Final Focused Verification

- `cargo test -p eos-runner --lib`: passed, 3 tests.
- `uv run pytest backend/tests/unit_test/test_engine/test_background_task_emitters.py backend/tests/unit_test/test_sandbox/test_daemon/test_bundle_upload.py backend/tests/unit_test/test_sandbox/test_provider/test_docker_adapter.py backend/tests/unit_test/test_sandbox/test_isolated_workspace_lifecycle_background.py backend/tests/unit_test/test_tools/test_hooks/test_require_no_inflight_background_tasks.py -q`:
  passed, 66 tests.
- `ruff check` over the touched engine, tool, Docker provider, benchmark, and
  focused test files: passed.

### Final Full Tiered Docker Live E2E

- Command family:
  `backend.tests.live_e2e_test._tools.run_tiered --provider docker --tier 0,1,2,3,4,5,6 --run-id phase3t-current-eos-paths-timeout-cancel-fix-tier0-6-20260601`.
- Environment:
  `EOS_SANDBOX_PROVIDER=docker`, `EOS_SANDBOX_RUNTIME=rust`,
  `EOS_LIVE_E2E_IMAGE=sweevo-dask__dask-10042:latest`,
  `EOS_DOCKER_PRIVILEGED=1`.
- Summary artifact:
  `.omc/results/progressive-test-summary-phase3t-current-eos-paths-timeout-cancel-fix-tier0-6-20260601.jsonl`.
- Tier result:
  - Tier 0 preflight: passed in 0.73 s.
  - Tier 1 smoke: passed in 12.23 s.
  - Tier 2 k-scaling spot check: passed in 12.63 s.
  - Tier 3 single-axis matrices: passed in 32.02 s.
  - Tier 4 cross-axis matrices: passed in 44.90 s.
  - Tier 5 soak: passed in 22.46 s.
  - Tier 6 adversarial: passed in 13.27 s.

### Final Live Docker PTY/Load/P95 Gate

- Report:
  `bench/phase3t-pty-command-docker-20260601-current-eos-paths-timeout-cancel-fix.json`.
- Top-level gate: passed.
- Correctness checks: passed.
  - finite writes publish through OCC and are readable.
  - finite `tty=false` `nohup ... 2>&1 &` descendant cleanup leaves no
    matching process.
  - PTY `tty=true` `nohup ... 2>&1 &` descendant cleanup leaves no matching
    process.
  - command environment resolves `python` to
    `/opt/miniconda3/envs/testbed/bin/python`.
  - stdout/stderr split remains correct.
- Operation p95s:
  - finite `exec_command(tty=false, cmd=true)`: 43.047 ms.
  - `exec_command(tty=true, cmd=true)`: 48.337 ms.
  - `check_pty_command_progress`: 1.781 ms.
  - `write_pty_command_stdin` to visible echo: 53.733 ms.
  - `cancel_pty_command`: 55.796 ms.
  - cancel cleanup: 381.024 ms.
- Load matrix: passed at 1/3/5/10 concurrency for finite no-op, finite write,
  and PTY no-op operations.

### Final Live PTY Notification Probe

- Natural PTY exit produced one notification in about 540 ms:
  `[BACKGROUND COMPLETED] pty_session_id="pty_1" status=ok exit_code=0`.
- PTY timeout produced one notification in about 1043 ms for a 1 s timeout:
  `[BACKGROUND COMPLETED] pty_session_id="pty_2" status=timed_out exit_code=124`.
- Explicit PTY cancel returned `status=cancelled` and produced no duplicate
  completion notification.

### Final Scope Note

- No Daytona run was performed.
- The final Rust live evidence covers the shared ephemeral workspace command
  path. Rust daemon isolated-workspace lifecycle ops are still not registered in
  the dispatcher, so a Rust PTY isolated-workspace comparison remains a later
  implementation gap; focused Python/IWS lifecycle tests passed for the local
  PTY-blocking behavior.

## 2026-06-01 Review Cleanup Pass

### Cleanup Changes

- Replaced the PTY cancel-only Python marker with a generic
  `mark_pty_result_reported_by_tool(...)` path. Any PTY control tool that
  observes a terminal result now marks the local PTY record delivered, so the
  query-loop completion bridge does not later emit a duplicate notification.
- Updated `write_pty_command_stdin` to claim a daemon completion that appears
  during its post-write yield window. If the PTY process exits quickly after
  receiving stdin and finalization has reached the daemon completion mailbox,
  the write tool can return the terminal `ok`/`timed_out`/`error` result instead
  of the legacy unconditional `running` response.
- Removed the obsolete `mark_pty_cancelled_by_tool(...)` path and updated
  tests to use the result-reported marker.
- Refreshed `docs/architecture/tools/background.html` so it no longer claims
  there is no automatic PTY completion notification path.

### Review Cleanup Verification

- Rebuilt package:
  `cargo run -p xtask -- package --target x86_64-unknown-linux-musl --out-dir dist --builder rust-lld`.
- New amd64 SHA-256 pinned in `runtime_artifact`:
  `0a7f5a17268ab097cd5d5918b2590ce9f90bcb86d23bdd79ea99de5d84a02585`.
- `cargo fmt --all --check`: passed.
- `cargo check -p eosd --target x86_64-unknown-linux-musl`: passed with
  pre-existing adjacent-crate warnings.
- `cargo test -p eos-runner --lib`: passed, 3 tests.
- `.venv/bin/python -m pytest backend/tests/unit_test/test_engine/test_background_task_emitters.py backend/tests/unit_test/test_engine/test_background_tasks.py backend/tests/unit_test/test_sandbox/test_api/test_command.py -q`:
  passed, 33 tests.
- `.venv/bin/python -m ruff check` over the touched background/PTY tool files
  and tests: passed.
- Focused live Docker PTY/load gate:
  `bench/phase3t-pty-command-docker-20260601-review-cleanup.json`,
  top-level gate passed. `write_pty_command_stdin` echo p95 was 53.365 ms.
- Explicit long-yield stdin edge probe:
  `write_pty_command_stdin(..., yield_time_ms=1000)` returned `status=ok`,
  `exit_code=0`, stdout containing `echo:edge`, and a subsequent
  `api.v1.pty.collect_completed` call returned no remaining completion for that
  PTY id.
