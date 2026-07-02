# LayerStack Squash Live-Docker Summary

- Run id: `squash-20260703-022647`
- Generated: `2026-07-03T02:28:38+08:00`
- Pytest exit status: `1`
- Cases: `21` run · `16` pass · `0` slow · `4` fail · `1` skipped

| Case | Tier | Status | Correctness | Space | Time |
| --- | --- | --- | --- | --- | --- |
| `HRD-01` | hard | FAIL | fail: {'error': {'kind': 'operation_failed', 'message': 'workspace session has active command sessions', 'details': {'active_command_session_ids': ['namespace_execution_7']}}} | fail:  | fail:  |
| `HRD-02` | hard | PASS | pass | pass | pass |
| `HRD-03` | hard | FAIL | fail: {'error': {'kind': 'operation_failed', 'message': 'workspace session has active command sessions', 'details': {'active_command_session_ids': ['namespace_execution_7']}}} | fail:  | fail:  |
| `HRD-04` | hard | FAIL | fail: {'error': {'kind': 'operation_failed', 'message': 'workspace session has active command sessions', 'details': {'active_command_session_ids': ['namespace_execution_7']}}} | fail:  | fail:  |
| `HRD-05` | hard | PASS | pass | pass | pass |
| `HRD-06` | hard | PASS | pass | pass | pass |
| `HRD-07` | hard | PASS | pass | pass | pass |
| `HRD-08` | hard | PASS | pass | pass | pass |
| `HRD-09` | hard | PASS | pass | pass | pass |
| `HRD-10` | hard | PASS | pass | pass | pass |
| `HRD-11` | hard | PASS | pass | pass | pass |
| `HRD-12` | hard | SKIP | pass | pass | pass |
| `HRD-13` | hard | PASS | pass | pass | pass |
| `HRD-14` | hard | PASS | pass | pass | pass |
| `HRD-15` | hard | FAIL | fail: {'error': {'kind': 'operation_failed', 'message': 'workspace session has active command sessions', 'details': {'active_command_session_ids': ['namespace_execution_7']}}} | fail:  | fail:  |
| `HRD-16` | hard | PASS | pass | pass | pass |
| `HRD-17` | hard | PASS | pass | pass | pass |
| `HRD-18` | hard | PASS | pass | pass | pass |
| `HRD-19` | hard | PASS | pass | pass | pass |
| `HRD-20` | hard | PASS | pass | pass | pass |
| `PRECONDITIONS` | preconditions | PASS | pass | pass | pass |

## Timing Distributions

| Timer | Count | P50 ms | P95 ms | Max ms |
| --- | ---: | ---: | ---: | ---: |
| `T_e2e` | 21 | 1785.409 | 12192.738 | 19665.327 |
| `T_quiesce` | 2 | 0.000 | 0.000 | 0.000 |
| `T_remount` | 2 | 1.000 | 1.000 | 1.000 |
| `T_squash` | 19 | 37.333 | 62.033 | 85.104 |
