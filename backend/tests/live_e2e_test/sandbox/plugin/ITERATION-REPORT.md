# Plugin Live E2E Iteration Report

## Iteration 6 - 2026-06-01 23:59 CST

- Exact command run: `EOS_SANDBOX_PROVIDER=docker EOS_LIVE_E2E_IMAGE=xingyaoww/sweb.eval.x86_64.dask_s_dask-10042:latest EOS_PLUGIN_REFRESH_SAMPLES=1 EOS_PLUGIN_REFRESH_AUTO_SQUASH_WRITES=104 uv run pytest -q -x -rs --tb=short --durations=10 backend/tests/live_e2e_test/sandbox/plugin/test_plugin_refresh_strategies.py`.
- Artifact paths inspected: pytest output and the refreshed `.omc/results/plugin-refresh-strategies-*` artifact emitted by the test.
- Pass/fail/skip status: passed; focused plugin live test `1 passed in 12.38s`.
- Findings summary: The benchmark-backed live plugin refresh case remains green after adding daemon-owned service process lifecycle behind `api.plugin.ensure start_services=true`. The live case still uses the Python refresh-strategy benchmark until the Rust harness accept/connect path is wired.
- Fix applied: None; this was a verification rerun after the Rust service lifecycle changes.
- Remaining risk or next iteration target: Wire the real plugin harness accept/connect path so a live Rust plugin case can start a service process, connect PPC, issue a read-only `plugin.*` op, and then exercise refresh.

## Iteration 5 - 2026-06-01 23:47 CST

- Exact command run: `EOS_SANDBOX_PROVIDER=docker EOS_LIVE_E2E_IMAGE=xingyaoww/sweb.eval.x86_64.dask_s_dask-10042:latest EOS_PLUGIN_REFRESH_SAMPLES=1 EOS_PLUGIN_REFRESH_AUTO_SQUASH_WRITES=104 uv run pytest -q -x -rs --tb=short --durations=10 backend/tests/live_e2e_test/sandbox/plugin/test_plugin_refresh_strategies.py`.
- Artifact paths inspected: pytest output and the refreshed `.omc/results/plugin-refresh-strategies-*` artifact emitted by the test.
- Pass/fail/skip status: passed; focused plugin live test `1 passed in 12.30s`.
- Findings summary: The existing Docker fixture plus benchmark-script path remains stable after the Rust daemon PPC route slice. The live evidence still covers `workspace_snapshot_refresh`, stale raw workspace watch behavior, commit-to-workspace timer unsuitability, auto-squash/post-drain materialization, and final cleanup.
- Fix applied: None; this was a verification rerun after the Rust PPC route changes.
- Remaining risk or next iteration target: Add a Rust-runtime live plugin case after daemon service spawn/reap is wired to real plugin harness processes.

## Iteration 4 - 2026-06-01 23:32 CST

- Exact command run: `EOS_SANDBOX_PROVIDER=docker EOS_LIVE_E2E_IMAGE=xingyaoww/sweb.eval.x86_64.dask_s_dask-10042:latest EOS_PLUGIN_REFRESH_SAMPLES=1 EOS_PLUGIN_REFRESH_AUTO_SQUASH_WRITES=104 uv run pytest -q -x -rs --tb=short --durations=10 backend/tests/live_e2e_test/sandbox/plugin/test_plugin_refresh_strategies.py`.
- Artifact paths inspected: pytest output and the refreshed `.omc/results/plugin-refresh-strategies-*` artifact emitted by the test.
- Pass/fail/skip status: passed; focused plugin live test `1 passed in 12.55s`.
- Findings summary: The pytest wrapper still reuses the Docker sandbox fixture and benchmark script successfully after the Rust registered-route documentation update. No new fixture setup, watcher visibility, auto-squash, or final cleanup regression appeared.
- Fix applied: None; this was a verification rerun.
- Remaining risk or next iteration target: Add the Rust-runtime live variant when process-backed PPC can execute behind the registered `plugin.*` routes.

## Iteration 3 - 2026-06-01 23:20 CST

- Exact command run: `EOS_SANDBOX_PROVIDER=docker EOS_LIVE_E2E_IMAGE=xingyaoww/sweb.eval.x86_64.dask_s_dask-10042:latest EOS_PLUGIN_REFRESH_SAMPLES=1 EOS_PLUGIN_REFRESH_AUTO_SQUASH_WRITES=104 uv run pytest -q -x -rs --tb=short --durations=10 backend/tests/live_e2e_test/sandbox/plugin/test_plugin_refresh_strategies.py`.
- Artifact paths inspected: `.omc/results/plugin-refresh-strategies-20260601T152044Z-6474.json`, `.omc/results/plugin-refresh-strategies-20260601T152044Z-6474.md`, `bench/plugin-refresh-strategies-20260601.json`, and `bench/plugin-refresh-strategies-20260601.md`.
- Pass/fail/skip status: passed; focused plugin live test `1 passed in 12.19s`; refreshed benchmark run with 3 samples also passed and wrote the `bench/` artifacts.
- Findings summary: The live suite now exercises the fast existing-container benchmark path from pytest instead of requiring a separate manual setup. The coverage proves `workspace_snapshot_refresh` keeps daemon reads current without raw workspace materialization, raw workspace watchers stay stale without materialization, `commit_to_workspace` is not a safe timer primitive, and auto-squash plus post-drain materialization preserves final bytes with zero orphan/missing layers.
- Verification result after the fix: Latest durable benchmark artifact recommends `workspace_snapshot_refresh`; p95 refresh was `5.747 ms` versus `commit_to_workspace` p95 `11.419 ms`; `workspace_snapshot_refresh.all_samples_ok=true`; `fs_watch_without_materialization.raw_workspace_stale=true`; `auto_squash_then_commit.gate_pass=true`; final active leases, orphan layers, and missing layers were all `0`.
- Remaining risk or next iteration target: The test still compares refresh strategies against the Python daemon path. Once Rust process-backed PPC lands, add a Rust-runtime variant that invokes `api.plugin.ensure`, one exact dynamic `plugin.*` read op, and one self-managed write callback.

## Iteration 2 - 2026-06-01 23:19 CST

- Exact command run: `EOS_SANDBOX_PROVIDER=docker EOS_LIVE_E2E_IMAGE=xingyaoww/sweb.eval.x86_64.dask_s_dask-10042:latest EOS_PLUGIN_REFRESH_SAMPLES=1 EOS_PLUGIN_REFRESH_AUTO_SQUASH_WRITES=104 uv run pytest -q -x -rs --tb=short --durations=10 backend/tests/live_e2e_test/sandbox/plugin/test_plugin_refresh_strategies.py`.
- Artifact paths inspected: pytest failure output; no benchmark JSON was produced because setup failed before watcher startup completed.
- Pass/fail/skip status: failed in `8.96s`.
- Findings summary: Recreating `/eos/plugin` immediately before `docker cp` was insufficient for fixture-created Docker containers. `docker exec` could create and see `/eos/plugin`, but `docker cp` still failed with `Could not find the file /eos/plugin in container`.
- Issue found: `docker cp` observes the container root namespace differently from the daemon/exec path for this fixture-created `/eos` runtime layout.
- Fix applied: Removed the watcher `docker cp` dependency and installed the watcher script through `docker exec` using Python/base64, so all setup happens through the same visibility path as the rest of the benchmark.

## Iteration 1 - 2026-06-01 23:18 CST

- Exact command run: `EOS_SANDBOX_PROVIDER=docker EOS_LIVE_E2E_IMAGE=xingyaoww/sweb.eval.x86_64.dask_s_dask-10042:latest EOS_PLUGIN_REFRESH_SAMPLES=1 EOS_PLUGIN_REFRESH_AUTO_SQUASH_WRITES=104 uv run pytest -q -x -rs --tb=short --durations=10 backend/tests/live_e2e_test/sandbox/plugin/test_plugin_refresh_strategies.py`.
- Artifact paths inspected: pytest failure output; no benchmark JSON was produced because setup failed before watcher startup completed.
- Pass/fail/skip status: failed in `8.55s`.
- Findings summary: The standalone benchmark passed on an older existing container, but the pytest fixture-created container failed installing the watcher script.
- Issue found: `docker cp local container:/eos/plugin/watch.py` failed because `/eos/plugin` was not visible to Docker copy even though the benchmark had just reset the experiment workspace.
- Fix applied: Added an idempotent watcher-parent directory creation step before copying. Iteration 2 proved that the issue was not directory creation alone but the `docker cp` path itself.
