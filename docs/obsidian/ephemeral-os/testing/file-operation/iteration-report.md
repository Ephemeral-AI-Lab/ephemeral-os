# File Operation Live E2E Iteration Report

## Test Rounds

| Round | Timestamp (CST) | Command | Scope | Result | Notes |
| --- | --- | --- | --- | --- | --- |
| 1 | 2026-07-02 06:57 | `pytest -m smoke` | Gateway smoke | `1 passed, 8 deselected in 2.31s` | Baseline gateway check before filling the matrix. |
| 2 | 2026-07-02 07:14 | `pytest runtime/file/smoke` | Baseline smoke + Linux session-only group | `15 passed, 5 skipped in 12.08s` | Darwin host skips Linux-only namespace/session cases. |
| 3 | 2026-07-02 07:15 | `pytest runtime/file/blame` | File blame group | `1 failed, 9 errors in 1.52s` | Setup errors came from a deleted generated shared workspace base cache while the gateway still had that path cached; one exercised test also showed dotted-path blame echoes the requested path field. |
| 4 | 2026-07-02 07:15 | `pytest runtime/file/blame` | File blame group rerun | `1 failed, 9 errors in 1.65s` | Recreated the host base-cache path, but default sandbox setup still failed with Docker unable to create the cached mount source; tmp-workspace sandbox creation still worked. |
| 5 | 2026-07-02 07:17 | `E2E_WORKSPACE_ROOT=/tmp/eos-file-operation-e2e-workspace pytest runtime/file/blame` | File blame group rerun | `1 failed, 8 passed, 1 xfailed in 15.98s` | Alternate workspace root unblocked sandbox setup. Remaining failure is test data ambiguity (`seed-1` also matching `seed-10`); xfail documents the dotted-path byte-identical response bug. |
| 6 | 2026-07-02 07:19 | `E2E_WORKSPACE_ROOT=/tmp/eos-file-operation-e2e-workspace pytest runtime/file/blame` | File blame group rerun | `9 passed, 1 xfailed in 22.11s` | Ladder test now edits a unique two-line window before each insert; xfail documents dotted-path blame echoing the requested path field. |
