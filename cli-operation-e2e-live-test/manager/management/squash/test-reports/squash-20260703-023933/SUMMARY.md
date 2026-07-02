# LayerStack Squash Live-Docker Summary

- Run id: `squash-20260703-023933`
- Generated: `2026-07-03T02:41:20+08:00`
- Pytest exit status: `1`
- Cases: `3` run · `1` pass · `0` slow · `2` fail · `0` skipped

| Case | Tier | Status | Correctness | Space | Time | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| `HRD-12` | hard | FAIL | fail: {"error": {"details": {}, "kind": "operation_failed", "message": "workspace setup failed at failed to finalize namespace execution: namespace runner --mount-overlay failed with exit code 1: ns-runner setns overlay mount failed: overlay mount failed"}}{"error":{"kind":"operation_failed","message":"workspace setup failed at failed to finalize namespace execution: namespace runner --mount-overlay failed with exit code 1: ns-runner setns overlay mount failed: overlay mount failed","details":{}}}
 | fail:  | fail:  |  |
| `MED-18` | medium | FAIL | fail: {"error": {"details": {}, "kind": "operation_failed", "message": "workspace setup failed at failed to finalize namespace execution: namespace runner --mount-overlay failed with exit code 1: ns-runner setns overlay mount failed: overlay mount failed"}}{"error":{"kind":"operation_failed","message":"workspace setup failed at failed to finalize namespace execution: namespace runner --mount-overlay failed with exit code 1: ns-runner setns overlay mount failed: overlay mount failed","details":{}}}
 | fail:  | fail:  |  |
| `PRECONDITIONS` | preconditions | PASS | pass | pass | pass |  |

## Timing Distributions

| Timer | Count | P50 ms | P95 ms | Max ms |
| --- | ---: | ---: | ---: | ---: |
| `T_e2e` | 3 | 51975.514 | 52144.136 | 52162.872 |
