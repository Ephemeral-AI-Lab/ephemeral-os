# SWE-EVO Real-Agent Loop — 2026-05-17

Starting state: branch `codex/fix-dot-path-normalization-tests` at `2cba70f5f` (`Skip test_sweevo_mock_agent_execution without EPHEMERALOS_DATABASE_URL`). Open worktree edits at bootstrap are `backend/src/task_center_runner/tests/sweevo/test_partial_parent_planner_full_only.py` and `backend/tests/unit_test/test_plugins/test_lsp_catalog.py`; neither is a primary editing surface for this loop, so they are left untouched. The CSV prompt bootstrap for `dask__dask_2023.3.2_2023.4.0` resolved to length `93150`.

## Iter 1 — 2026-05-17 00:45

**Hypothesis:** baseline — no edits, observe what breaks first.
**Primary surface touched:** none — infra-only
**Infra patches (if any):**
- `backend/src/benchmarks/sweevo/__main__.py:393` skip snapshot preflight for images without explicit non-latest versions so CSV runner can use the existing direct-image sandbox path.
- `backend/tests/unit_test/test_benchmarks/test_sweevo_csv_runner_cli.py:165` add coverage that bare images do not call snapshot verification.
**Change-set:**
- `backend/src/benchmarks/sweevo/__main__.py`
- `backend/tests/unit_test/test_benchmarks/test_sweevo_csv_runner_cli.py`

**Run outcome:**
- resolved: false
- f2p: n/a
- p2p_broken: n/a
- duration_s: 2
- status: failed
- terminal failure mode: bootstrap failed because Daytona snapshot `sweevo-dask__dask-10042` is not registered.

**Checklist scores (§2):**
1. planner-terminal: n/a (bootstrap stopped before TaskCenter)
2. planner-explore: n/a (bootstrap stopped before TaskCenter)
3. planner-dag: n/a (bootstrap stopped before TaskCenter)
4. planner-task-specs: n/a (bootstrap stopped before TaskCenter)
5. executor-terminal: n/a (bootstrap stopped before TaskCenter)
6. verifier-terminal: n/a (bootstrap stopped before TaskCenter)
7. evaluator-terminal: n/a (bootstrap stopped before TaskCenter)
8. nesting+parallelism: n/a (bootstrap stopped before TaskCenter)
9. context-engine: n/a (bootstrap stopped before TaskCenter)
10. perf: n/a (bootstrap stopped before sandbox creation)

**Top finding (the one thing to fix next):** The dask dataset image is a bare Docker Hub repo with only `latest`; Daytona rejects bare refs and explicit `:latest` for snapshot creation, and digest snapshot registration is forbidden in this account. The CSV runner was the only benchmark path that forced snapshot preflight before reaching the existing direct-image fallback.
**Next hypothesis:** after allowing CSV runner to skip snapshot preflight for bare images, the same baseline command will reach sandbox provisioning and produce a TaskCenter audit tree.
**Audit refs:** `.omc/experiments/sweevo-real-agent-loop-20260517/iter-1/console.log`

**Guard:** `.venv/bin/pytest backend/tests/unit_test/test_benchmarks/test_sweevo_csv_runner_cli.py -q` -> `10 passed in 0.44s`.
