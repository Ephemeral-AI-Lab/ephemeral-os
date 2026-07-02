# LayerStack Squash Live-Docker Summary

- Run id: `squash-20260703-043423`
- Generated: `2026-07-03T04:34:38+08:00`
- Pytest exit status: `1`
- Cases: `2` run · `1` pass · `0` slow · `1` fail · `0` skipped

| Case | Tier | Status | Correctness | Space | Time | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| `LOAD-COMBO-HTTP` | hard | FAIL | fail: {'error': {'kind': 'internal_error', 'message': 'sandbox daemon forwarding failed: connect 127.0.0.1:58591 failed: Connection refused (os error 61)', 'details': {}}} | fail:  | fail:  |  |
| `PRECONDITIONS` | preconditions | PASS | pass | pass | pass |  |

## Timing Distributions

| Timer | Count | P50 ms | P95 ms | Max ms |
| --- | ---: | ---: | ---: | ---: |
| `T_e2e` | 2 | 7254.412 | 12891.069 | 13517.364 |
| `T_http_disconnect` | 1 | 9.441 | 9.441 | 9.441 |
| `T_squash` | 1 | 154.059 | 154.059 | 154.059 |
| `T_squash_invocation_1` | 1 | 154.059 | 154.059 | 154.059 |
