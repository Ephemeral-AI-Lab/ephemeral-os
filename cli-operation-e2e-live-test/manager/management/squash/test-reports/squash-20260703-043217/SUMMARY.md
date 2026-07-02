# LayerStack Squash Live-Docker Summary

- Run id: `squash-20260703-043217`
- Generated: `2026-07-03T04:32:36+08:00`
- Pytest exit status: `1`
- Cases: `2` run · `1` pass · `0` slow · `1` fail · `0` skipped

| Case | Tier | Status | Correctness | Space | Time | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| `LOAD-COMBO-HTTP` | hard | FAIL | fail: {'error': {'kind': 'internal_error', 'message': 'sandbox daemon forwarding failed: daemon returned an empty response', 'details': {}}} | fail:  | fail:  |  |
| `PRECONDITIONS` | preconditions | PASS | pass | pass | pass |  |

## Timing Distributions

| Timer | Count | P50 ms | P95 ms | Max ms |
| --- | ---: | ---: | ---: | ---: |
| `T_e2e` | 2 | 9070.981 | 16095.896 | 16876.442 |
| `T_http_disconnect` | 1 | 10.827 | 10.827 | 10.827 |
| `T_squash` | 1 | 629.727 | 629.727 | 629.727 |
| `T_squash_invocation_1` | 1 | 629.727 | 629.727 | 629.727 |
