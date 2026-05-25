# Isolated Workspace Holder Fast-Fallback Report

Generated: 2026-05-25T12:58:33Z

## Summary

The old slow holder fallback came from the default isolated-workspace exit
grace window, not from normal isolated-workspace entry. Normal holder exit is
fast when `ns_holder` can handle SIGTERM. The visible 5 second case happens
when the holder is stopped or wedged, so `kill_holder()` waits the configured
grace window before escalating to SIGKILL.

The fix makes normal `api.isolated_workspace.exit` use a short configurable
grace window: `EOS_ISOLATED_WORKSPACE_EXIT_GRACE_S`, defaulting to `0.25`.
Deployments can still opt into a longer window by setting that env var, but
the default no longer pays the old 5 second wait.

## Root Cause

- `IsolatedPipeline.exit()` defaulted to `grace_s=5.0`.
- `_LinuxRuntime.kill_holder()` sends SIGTERM, waits `grace_s`, then SIGKILLs
  if the holder did not exit.
- The failure-mode test deliberately `SIGSTOP`s the holder, making it unable to
  handle SIGTERM. That forced the fallback path and exposed the full grace wait.

## Changes

- `backend/src/sandbox/isolated_workspace/helper/types.py`
  - Added `exit_grace_s` to `_ManagerConfig`.
  - Added `EOS_ISOLATED_WORKSPACE_EXIT_GRACE_S`, default `0.25`.
- `backend/src/sandbox/isolated_workspace/helper/lifecycle.py`
  - Changed `exit()` to use configured `exit_grace_s` unless an explicit
    `grace_s` is passed.
- `backend/src/task_center_runner/tests/mock/sandbox/isolated_workspace/conftest.py`
  - Added `EOS_ISOLATED_WORKSPACE_EXIT_GRACE_S` to the per-test env cleanup set.
- `backend/src/task_center_runner/tests/mock/sandbox/isolated_workspace/failure_modes/test_holder_refuses_sigterm_sigkill_fallback.py`
  - Updated the test to assert bounded fallback latency instead of permitting
    the old 5 second behavior.
- `backend/tests/unit_test/test_sandbox/test_isolated_pipeline_unified_lifecycle.py`
  - Added config coverage for the new default and env override.

## Verification

Commands run:

```bash
uv run python -m py_compile \
  backend/src/sandbox/isolated_workspace/helper/types.py \
  backend/src/sandbox/isolated_workspace/helper/lifecycle.py \
  backend/src/task_center_runner/tests/mock/sandbox/isolated_workspace/failure_modes/test_holder_refuses_sigterm_sigkill_fallback.py \
  backend/tests/unit_test/test_sandbox/test_isolated_pipeline_unified_lifecycle.py

uv run pytest \
  backend/tests/unit_test/test_sandbox/test_isolated_pipeline_unified_lifecycle.py \
  backend/tests/unit_test/test_sandbox/test_isolated_runtime.py \
  -q

EOS_SWEEVO_INSTANCE=dask__dask_2023.3.2_2023.4.0 \
EOS_SANDBOX_PROVIDER=docker \
EOS_ISOLATED_WORKSPACE_ENABLED=true \
EOS__RUNNER__LIVE_E2E__HEAVY_ENABLED=true \
EOS__RUNNER__SANDBOX_REUSE_MODE=reuse \
EPHEMERALOS_DATABASE_URL='sqlite:///./.ephemeralos/ephemeralos.db' \
uv run pytest -q -s --tb=short -p no:randomly \
  backend/src/task_center_runner/tests/mock/sandbox/isolated_workspace/failure_modes/test_holder_refuses_sigterm_sigkill_fallback.py

git diff --check -- \
  backend/src/sandbox/isolated_workspace/helper/types.py \
  backend/src/sandbox/isolated_workspace/helper/lifecycle.py \
  backend/src/task_center_runner/tests/mock/sandbox/isolated_workspace/conftest.py \
  backend/tests/unit_test/test_sandbox/test_isolated_pipeline_unified_lifecycle.py \
  backend/src/task_center_runner/tests/mock/sandbox/isolated_workspace/failure_modes/test_holder_refuses_sigterm_sigkill_fallback.py
```

Results:

- Unit slice: `8 passed in 0.19s`.
- Live holder fallback test: `1 passed in 21.24s`.
- Diff whitespace check: passed.

## Live Audit Evidence

Audit source:

```text
/tmp/sandbox_isolated_workspace_events.jsonl
```

Active SWE-EVO container:

```text
4bc1574dbc0f sweevo-test-dask__dask_2023.3.2_2023.4.0-c57d421f
```

Latest forced-fallback exit event:

```json
{
  "handle": "0991d29f699343d2",
  "total_ms": 326.9907089998014,
  "kill_holder_ms": 274.6203750011773,
  "phases_ms": {
    "kill_holder": 274.6203750011773,
    "teardown_veth": 49.766874999477295,
    "release_snapshot": 0.8733330014365492,
    "cgroup_rmdir": 0.18687500050873496,
    "rmtree_scratch": 0.8414160001848359
  }
}
```

This proves the forced holder fallback now waits roughly the configured short
grace window plus process cleanup overhead, instead of the previous 5 second
default.

## Performance Notes

- Normal isolated-workspace enter remains unchanged.
- Normal holder exit remains fast when SIGTERM is handled.
- The change bounds the exceptional stopped-holder path.
- No workspace materialization or copy behavior changed; this patch does not
  affect O(1) disk behavior.
