# LayerStack Squash Live-Docker Summary

- Run id: `squash-20260703-043312`
- Generated: `2026-07-03T04:33:31+08:00`
- Pytest exit status: `1`
- Cases: `2` run · `1` pass · `0` slow · `1` fail · `0` skipped

| Case | Tier | Status | Correctness | Space | Time | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| `LOAD-COMBO-HTTP` | hard | FAIL | fail: {'error': {'kind': 'internal_error', 'message': 'sandbox daemon forwarding failed: connect 127.0.0.1:58587 failed: Connection refused (os error 61)', 'details': {}}} | fail:  | fail:  |  |
| `PRECONDITIONS` | preconditions | PASS | pass | pass | pass |  |

## Timing Distributions

| Timer | Count | P50 ms | P95 ms | Max ms |
| --- | ---: | ---: | ---: | ---: |
| `T_e2e` | 2 | 9110.622 | 16160.824 | 16944.180 |
| `T_http_disconnect` | 1 | 11.040 | 11.040 | 11.040 |
| `T_squash` | 1 | 664.982 | 664.982 | 664.982 |
| `T_squash_invocation_1` | 1 | 664.982 | 664.982 | 664.982 |
