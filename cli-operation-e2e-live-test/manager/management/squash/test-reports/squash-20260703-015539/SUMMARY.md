# LayerStack Squash Live-Docker Summary

- Run id: `squash-20260703-015539`
- Generated: `2026-07-03T01:55:57+08:00`
- Pytest exit status: `1`
- Cases: `11` run · `9` pass · `0` slow · `2` fail · `0` skipped

| Case | Tier | Status | Correctness | Space | Time |
| --- | --- | --- | --- | --- | --- |
| `PRECONDITIONS` | preconditions | PASS | pass | pass | pass |
| `SMK-01` | smoke | PASS | pass | pass | pass |
| `SMK-02` | smoke | PASS | pass | pass | pass |
| `SMK-03` | smoke | PASS | pass | pass | pass |
| `SMK-04` | smoke | PASS | pass | pass | pass |
| `SMK-05` | smoke | PASS | pass | n/a | pass |
| `SMK-06` | smoke | PASS | pass | pass | pass |
| `SMK-07` | smoke | FAIL | fail: {'error': {'kind': 'operation_failed', 'message': 'workspace session has active command sessions', 'details': {'active_command_session_ids': ['namespace_execution_4']}}} | fail:  | fail:  |
| `SMK-08` | smoke | PASS | pass | pass | pass |
| `SMK-09` | smoke | PASS | pass | pass | pass |
| `SMK-10` | smoke | FAIL | fail: {'error': {'kind': 'internal_error', 'message': 'sandbox daemon forwarding failed: connect 127.0.0.1:57342 failed: Connection refused (os error 61)', 'details': {}}} | fail:  | fail:  |

## Timing Distributions

| Timer | Count | P50 ms | P95 ms | Max ms |
| --- | ---: | ---: | ---: | ---: |
| `T_e2e` | 11 | 1290.526 | 2059.179 | 2593.415 |
| `T_quiesce` | 2 | 0.000 | 0.000 | 0.000 |
| `T_remount` | 2 | 1.000 | 1.000 | 1.000 |
| `T_squash` | 10 | 35.089 | 45.732 | 46.559 |
