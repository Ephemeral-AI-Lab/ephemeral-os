# LayerStack Squash Live-Docker Summary

- Run id: `squash-20260703-015653`
- Generated: `2026-07-03T01:57:36+08:00`
- Pytest exit status: `1`
- Cases: `21` run · `16` pass · `0` slow · `5` fail · `0` skipped

| Case | Tier | Status | Correctness | Space | Time |
| --- | --- | --- | --- | --- | --- |
| `MED-01` | medium | FAIL | pass | fail: layers_bytes 4->0, tolerance=0.25 | fail:  |
| `MED-02` | medium | FAIL | fail: opaque xattr='' | fail:  | fail:  |
| `MED-03` | medium | FAIL | fail: {'status': 'error', 'exit_code': 1, 'wall_time_seconds': 0.04347675, 'command_total_time_seconds': 0.027775833, 'start_offset': 0, 'end_offset': 0, 'total_lines': 0, 'original_token_count': 0, 'output': ''} | fail:  | fail:  |
| `MED-04` | medium | PASS | pass | pass | pass |
| `MED-05` | medium | PASS | pass | pass | pass |
| `MED-06` | medium | PASS | pass | pass | pass |
| `MED-07` | medium | PASS | pass | pass | pass |
| `MED-08` | medium | PASS | pass | pass | pass |
| `MED-09` | medium | PASS | pass | pass | pass |
| `MED-10` | medium | PASS | pass | pass | pass |
| `MED-11` | medium | FAIL | fail: {'error': {'kind': 'operation_failed', 'message': 'workspace session has active command sessions', 'details': {'active_command_session_ids': ['namespace_execution_4']}}} | fail:  | fail:  |
| `MED-12` | medium | PASS | pass | pass | pass |
| `MED-13` | medium | PASS | pass | pass | pass |
| `MED-14` | medium | PASS | pass | pass | pass |
| `MED-15` | medium | PASS | pass | pass | pass |
| `MED-16` | medium | PASS | pass | pass | pass |
| `MED-17` | medium | PASS | pass | pass | pass |
| `MED-18` | medium | PASS | pass | pass | pass |
| `MED-19` | medium | PASS | pass | pass | pass |
| `MED-20` | medium | FAIL | fail: {'error': {'kind': 'operation_failed', 'message': 'workspace session has active command sessions', 'details': {'active_command_session_ids': ['namespace_execution_4']}}} | fail:  | fail:  |
| `PRECONDITIONS` | preconditions | PASS | pass | pass | pass |

## Timing Distributions

| Timer | Count | P50 ms | P95 ms | Max ms |
| --- | ---: | ---: | ---: | ---: |
| `T_e2e` | 21 | 1410.962 | 2713.455 | 4608.033 |
| `T_quiesce` | 3 | 0.000 | 0.900 | 1.000 |
| `T_remount` | 2 | 1.000 | 1.000 | 1.000 |
| `T_squash` | 20 | 36.255 | 193.806 | 1317.003 |
