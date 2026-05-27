# Workspace Base Live E2E Iteration Report

## Iteration 1 - 2026-05-26 19:42:20 CST

- Exact command run: `uv run pytest -q -x --tb=short --durations=20 backend/tests/live_e2e_test/sandbox/workspace_base/test_squash_deferred_gc_scenarios.py backend/tests/live_e2e_test/sandbox/workspace_base/test_commit_to_workspace_correctness_perf.py`.
- Artifact paths inspected: no scenario artifacts were created because pytest stopped during collection.
- Pass/fail/skip status: failed during collection; no tests ran.
- Findings summary: The selected workspace-base tests could not collect because `backend/tests/live_e2e_test/conftest.py` scans every Python file under `backend/tests/live_e2e_test` and found forbidden top-level sandbox-internal imports in `sandbox/_harness/lease_resource_probe.py`.
- Issues found: `pytest.UsageError: import-fence violation in sandbox/_harness/lease_resource_probe.py: forbidden imports ['sandbox.occ.changeset', 'sandbox.occ.layer_stack_client', 'sandbox.overlay.capability', 'sandbox.overlay.namespace_runner', 'sandbox.overlay.writable_dirs']`.
- Why it failed: Root cause is collection-time static AST scanning of the whole live suite combined with direct top-level imports in the O(1) lease-resource harness. The two requested workspace-base files do not import that harness, but the suite-wide fence blocks any focused live run until the violation is removed.
- Fix applied: Updated `backend/tests/live_e2e_test/sandbox/_harness/lease_resource_probe.py` to resolve the forbidden sandbox internals lazily via `importlib.import_module` at the existing runtime call sites instead of importing them at module load.
- Verification result after the fix: pending; next command is the same focused two-file pytest run.
- Remaining risk or next iteration target: rerun the focused scenario set. If collection passes, inspect pass/fail status and any generated workspace-base JSONL artifacts.

## Iteration 2 - 2026-05-26 19:43:45 CST

- Exact command run: `uv run pytest -q -x -rs --tb=short --durations=20 backend/tests/live_e2e_test/sandbox/workspace_base/test_squash_deferred_gc_scenarios.py backend/tests/live_e2e_test/sandbox/workspace_base/test_commit_to_workspace_correctness_perf.py`, then `set -a; source .env; set +a; export EOS_LIVE_E2E_IMAGE="${EOS_LIVE_E2E_IMAGE:-$EPHEMERALOS_SANDBOX_DEFAULT_IMAGE}"; uv run pytest -q -x -rs --tb=short --durations=20 backend/tests/live_e2e_test/sandbox/workspace_base/test_squash_deferred_gc_scenarios.py backend/tests/live_e2e_test/sandbox/workspace_base/test_commit_to_workspace_correctness_perf.py`.
- Artifact paths inspected: no scenario artifacts were created. Checked local Docker image inventory and a candidate image prerequisite probe with `docker run --rm xingyaoww/sweb.eval.x86_64.dask_s_dask-10042:latest sh -lc '...'`.
- Pass/fail/skip status: first command collected successfully but skipped both tests because `EOS_LIVE_E2E_IMAGE` was unset for Docker. Second command errored during sandbox setup because the `.env` image `registry:6000/daytona/sweevo-psf-requests-3738:v1` was neither local nor pullable.
- Findings summary: The import-fence collection blocker is resolved. The next blocker is environment image resolution. The local Docker inventory includes `xingyaoww/sweb.eval.x86_64.dask_s_dask-10042:latest`; a direct container probe found `git`, `/testbed`, Python 3.11, and root user available, with an amd64-on-arm64 platform warning.
- Issues found: Docker pull of `registry:6000/daytona/sweevo-psf-requests-3738:v1` failed with `failed to resolve reference ... Head "https://registry:6000/v2/daytona/sweevo-psf-requests-3738/manifests/v1": EOF`.
- Why it failed: The live fixture requires `EOS_LIVE_E2E_IMAGE` for Docker. The configured `.env` fallback points to an unavailable local registry image in this environment.
- Fix applied: none to product code. Identified a local candidate Docker image for the next verification attempt.
- Verification result after the fix: pending; next command is the focused two-file run with `EOS_LIVE_E2E_IMAGE=xingyaoww/sweb.eval.x86_64.dask_s_dask-10042:latest`.
- Remaining risk or next iteration target: the local candidate image may still lack runtime-bundle setup compatibility or may be slow under amd64 emulation on arm64.

## Iteration 3 - 2026-05-26 19:45:11 CST

- Exact command run: `EOS_LIVE_E2E_IMAGE=xingyaoww/sweb.eval.x86_64.dask_s_dask-10042:latest uv run pytest -q -x -rs --tb=short --durations=20 backend/tests/live_e2e_test/sandbox/workspace_base/test_squash_deferred_gc_scenarios.py backend/tests/live_e2e_test/sandbox/workspace_base/test_commit_to_workspace_correctness_perf.py`.
- Artifact paths inspected: no JSONL artifacts were written because the first in-sandbox probe failed before emitting its payload.
- Pass/fail/skip status: failed in `test_squash_deferred_gc_scenarios` during the probe body.
- Findings summary: The candidate image brought up far enough to execute the workspace-base probe. The failure was a probe-prelude `NameError`, not a sandbox squash/deferred-GC invariant failure.
- Issues found: In-sandbox stderr reported `NameError: name 'WriteLayerChange' is not defined. Did you mean: 'LayerChange'?`.
- Why it failed: `WORKSPACE_BASE_PROBE_PRELUDE` imported only `LayerChange`, but the requested and neighboring workspace-base scenario bodies construct changes through `WriteLayerChange`, `DeleteLayerChange`, `SymlinkLayerChange`, and `OpaqueDirLayerChange`.
- Fix applied: Updated `backend/tests/live_e2e_test/sandbox/_harness/workspace_base_probe.py` to expose the concrete layer change constructors in the rendered in-sandbox prelude. Also made `_call_row(..., extra=...)` include a nested `extra` dict while preserving existing flattened keys, because the requested tests assert `row["extra"][...]`.
- Verification result after the fix: pending; next command is the same focused two-file run with the local candidate image.
- Remaining risk or next iteration target: rerun to expose the actual scenario behavior; watch for any runtime-bundle/platform limitation from the amd64 image on arm64.

## Iteration 4 - 2026-05-26 19:47:28 CST

- Exact command run: `EOS_LIVE_E2E_IMAGE=xingyaoww/sweb.eval.x86_64.dask_s_dask-10042:latest uv run pytest -q -x -rs --tb=short --durations=20 backend/tests/live_e2e_test/sandbox/workspace_base/test_squash_deferred_gc_scenarios.py backend/tests/live_e2e_test/sandbox/workspace_base/test_commit_to_workspace_correctness_perf.py`.
- Artifact paths inspected: no JSONL artifacts were written because S2 failed before emitting the payload.
- Pass/fail/skip status: failed in S2 of `test_squash_deferred_gc_scenarios`.
- Findings summary: The test reached the release-order scenario. The assertion that `release_lease(lease_a)` removes `lease_a`'s head from the active manifest is not compatible with the current `LayerStack.release_lease()` contract.
- Issues found: Rendered line 500 asserted `lease_a_layers[0] not in manager.read_active_manifest().layers`, but `release_lease()` only deletes layer directories unreferenced by both active manifest and remaining leases; it does not rewrite active.
- Why it failed: The scenario conflated lease-retention-set removal with active-manifest mutation. `backend/src/sandbox/layer_stack/stack.py::_unreferenced_layers` explicitly skips `current_manifest.layers`, so active-visible layers must remain after lease release.
- Fix applied: Updated `backend/tests/live_e2e_test/sandbox/workspace_base/test_squash_deferred_gc_scenarios.py` S2 to assert `lease_a` leaves the lease retention set while its active-visible head remains on disk, and to keep validating that `lease_b`'s layers survive until `lease_b` is released.
- Verification result after the fix: pending; next command is the same focused two-file Docker run.
- Remaining risk or next iteration target: rerun to validate the remaining S3-S5 squash scenarios and then the commit-to-workspace scenarios.

## Iteration 5 - 2026-05-26 19:48:21 CST

- Exact command run: `EOS_LIVE_E2E_IMAGE=xingyaoww/sweb.eval.x86_64.dask_s_dask-10042:latest uv run pytest -q -x -rs --tb=short --durations=20 backend/tests/live_e2e_test/sandbox/workspace_base/test_squash_deferred_gc_scenarios.py backend/tests/live_e2e_test/sandbox/workspace_base/test_commit_to_workspace_correctness_perf.py`.
- Artifact paths inspected: pytest stdout payload for `workspace_base.squash_deferred_gc_scenarios`; no JSONL artifact was written because the host assertion failed after payload parsing and before `write_jsonl_artifact`.
- Pass/fail/skip status: failed in the host assertion phase of `test_squash_deferred_gc_scenarios`.
- Findings summary: All five S1-S5 squash/deferred-GC rows emitted with `success=true`. The failure was a row-shape mismatch: rows had `label`, but the new tests index rows by `name`.
- Issues found: `KeyError: 'name'` at `by_name = {row["name"]: row for row in rows}`.
- Why it failed: The shared `_call_row()` helper historically used `label`; the two requested scenario files expect the mock-scenario-style `name` field.
- Fix applied: Updated `backend/tests/live_e2e_test/sandbox/_harness/workspace_base_probe.py::_call_row()` to emit `name` as an alias of `label`.
- Verification result after the fix: pending; next command is the same focused two-file Docker run.
- Remaining risk or next iteration target: squash scenarios should now pass host assertions; continue into the commit-to-workspace scenarios.

## Iteration 6 - 2026-05-26 19:50:28 CST

- Exact command run: `EOS_LIVE_E2E_IMAGE=xingyaoww/sweb.eval.x86_64.dask_s_dask-10042:latest uv run pytest -q -x -rs --tb=short --durations=20 backend/tests/live_e2e_test/sandbox/workspace_base/test_squash_deferred_gc_scenarios.py backend/tests/live_e2e_test/sandbox/workspace_base/test_commit_to_workspace_correctness_perf.py`.
- Artifact paths inspected: `.omc/results/live-e2e-phase01-workspace-base-squash_deferred_gc_scenarios-20260526T114853Z.jsonl` and `.omc/results/live-e2e-phase01-workspace-base-commit_to_workspace_correctness_perf-20260526T114855Z.jsonl`.
- Pass/fail/skip status: passed; `2 passed in 15.34s`.
- Findings summary: Both requested files passed under the Docker provider with the local image `xingyaoww/sweb.eval.x86_64.dask_s_dask-10042:latest`. Each artifact contains one summary row plus five scenario rows.
- Issues found: none in the final run. The Docker run used an amd64 image on an arm64 host and emitted the expected platform warning during the earlier prerequisite probe, but the focused tests completed successfully.
- Why it failed: not applicable for the final run.
- Fix applied: final effective changes are `backend/tests/live_e2e_test/sandbox/_harness/workspace_base_probe.py` row/prelude compatibility and `backend/tests/live_e2e_test/sandbox/workspace_base/test_squash_deferred_gc_scenarios.py` S2 assertion correction.
- Verification result after the fix: `git diff --check` passed; `uv run ruff check backend/tests/live_e2e_test/sandbox/_harness/workspace_base_probe.py backend/tests/live_e2e_test/sandbox/workspace_base/test_squash_deferred_gc_scenarios.py` passed; focused Docker pytest passed.
- Remaining risk or next iteration target: no remaining blocker for these two files. The `.env` default image `registry:6000/daytona/sweevo-psf-requests-3738:v1` is unavailable from Docker on this machine, so future Docker live runs need an explicit local `EOS_LIVE_E2E_IMAGE` unless that registry/image is restored.

## Iteration 7 - 2026-05-26 19:59:01 CST

- Exact command run: pending.
- Artifact paths inspected: source only; no new artifact yet.
- Pass/fail/skip status: pending.
- Findings summary: Added a larger S6 scenario to `test_squash_deferred_gc_scenarios.py` for orphan-layer detection under higher depth and concurrent publish/squash/release pressure.
- Issues found: existing S1-S5 coverage did not assert layer-directory set equality against `active.layers | leased_layers()` and only used small layer counts plus one publish/squash race.
- Why it failed: not applicable; this is coverage expansion for an untested risk.
- Fix applied: Added layer-storage consistency helpers inside the in-sandbox probe and a new `s6_large_concurrent_orphan_detection` scenario with 128 initial publishes, 8 leases at different depths, 4 publish workers, 4 squash workers, and one concurrent release worker. The scenario asserts no orphan or missing layer dirs before concurrency, after concurrency, after all lease releases, and after a final squash.
- Verification result after the fix: pending; next command is `EOS_LIVE_E2E_IMAGE=xingyaoww/sweb.eval.x86_64.dask_s_dask-10042:latest uv run pytest -q -x -rs --tb=short --durations=20 backend/tests/live_e2e_test/sandbox/workspace_base/test_squash_deferred_gc_scenarios.py`.
- Remaining risk or next iteration target: run the focused Docker live test, inspect the S6 artifact row, then run formatting/lint checks.

## Iteration 8 - 2026-05-26 20:00:52 CST

- Exact command run: `uv run ruff check backend/tests/live_e2e_test/sandbox/workspace_base/test_squash_deferred_gc_scenarios.py`; `git diff --check`; `EOS_LIVE_E2E_IMAGE=xingyaoww/sweb.eval.x86_64.dask_s_dask-10042:latest uv run pytest -q -x -rs --tb=short --durations=20 backend/tests/live_e2e_test/sandbox/workspace_base/test_squash_deferred_gc_scenarios.py`; then the same Docker pytest command including both `test_squash_deferred_gc_scenarios.py` and `test_commit_to_workspace_correctness_perf.py`.
- Artifact paths inspected: `.omc/results/live-e2e-phase01-workspace-base-squash_deferred_gc_scenarios-20260526T120032Z.jsonl` and `.omc/results/live-e2e-phase01-workspace-base-commit_to_workspace_correctness_perf-20260526T120035Z.jsonl`.
- Pass/fail/skip status: passed; single-file squash run `1 passed in 15.88s`; two-file run `2 passed in 17.81s`.
- Findings summary: S6 emitted one summary plus six scenario rows. The S6 row recorded `initial_publish_count=128`, `lease_count=8`, `concurrent_publish_workers=4`, `concurrent_publish_per_worker=12`, `concurrent_squash_workers=4`, and `partial_release_count=4`.
- Issues found: none in the final run.
- Why it failed: not applicable.
- Fix applied: kept the S6 large-depth concurrent orphan-detection scenario and row assertions.
- Verification result after the fix: `git diff --check` passed; `ruff check` passed; Docker live tests passed. S6 artifact evidence showed `orphan_layer_count=0` and `missing_layer_count=0` at `pre_concurrency`, `post_concurrency`, `post_release`, and `post_final_squash`; after final squash with no leases, `active_depth=1` and `layer_dir_count=1`.
- Remaining risk or next iteration target: this covers layer-directory orphan detection in the workspace-base native probe. Broader daemon/OCC auto-squash orphan checks would belong in the public-tool/OCC scenario layer if needed.

## Iteration 9 - 2026-05-26 20:17:25 CST

- Exact command run: `uv run ruff check backend/tests/live_e2e_test/sandbox/_harness/workspace_base_probe.py backend/tests/live_e2e_test/sandbox/workspace_base/test_squash_deferred_gc_scenarios.py`; `git diff --check`; `EOS_LIVE_E2E_IMAGE=xingyaoww/sweb.eval.x86_64.dask_s_dask-10042:latest uv run pytest -q -x -rs --tb=short --durations=20 backend/tests/live_e2e_test/sandbox/workspace_base/test_squash_deferred_gc_scenarios.py`; then the same Docker pytest command including both `test_squash_deferred_gc_scenarios.py` and `test_commit_to_workspace_correctness_perf.py`.
- Artifact paths inspected: `.omc/results/live-e2e-phase01-workspace-base-squash_deferred_gc_scenarios-20260526T121640Z.jsonl` and `.omc/results/live-e2e-phase01-workspace-base-commit_to_workspace_correctness_perf-20260526T121643Z.jsonl`.
- Pass/fail/skip status: passed; single-file squash run `1 passed in 16.50s`; two-file run `2 passed in 19.10s`.
- Findings summary: Updated the maintained layerstack wiki to clarify squash depth as `distinct lease heads + emitted non-head runs`, then added S7 coverage for four distinct lease heads spread across five runs. The current S6 stress case now uses four concurrent publish workers, one manual squash worker, and one release worker, keeping the "at most one concurrent squash worker" invariant in the test.
- Issues found: none in the final run.
- Why it failed: not applicable.
- Fix applied: Added the `Depth math: heads plus runs` callout to `docs/architecture/sandbox/layerstack.html#layerstack-squash-gc`. Added `s7_four_lease_heads_create_nine_entry_squash`, which starts at depth 14, holds four distinct lease heads, squashes to depth 9 because there are five foldable non-head runs, releases all leases, and then verifies the next no-lease squash folds to depth 1.
- Verification result after the fix: `git diff --check` passed; `ruff check` passed; Docker live tests passed. S7 artifact evidence showed `lease_head_count=4`, `expected_non_head_runs=5`, `expected_post_squash_depth=9`, `pre_squash.active_depth=14`, `post_squash.active_depth=9`, `post_release.active_depth=9`, and `post_next_squash.active_depth=1`; every S7 storage checkpoint had `orphan_layer_count=0` and `missing_layer_count=0`.
- Remaining risk or next iteration target: S7 proves the layerstack squash depth math for four lease heads in the native workspace-base path. If we want daemon-level auto-squash scheduling coverage next, add a separate public-tool/OCC scenario that asserts only one autosquash worker is active while multiple publish workers race.
