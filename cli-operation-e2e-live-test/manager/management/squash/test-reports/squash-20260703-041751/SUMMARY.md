# LayerStack Squash Live-Docker Summary

- Run id: `squash-20260703-041751`
- Generated: `2026-07-03T04:18:12+08:00`
- Pytest exit status: `0`
- Cases: `3` run · `3` pass · `0` slow · `0` fail · `0` skipped

| Case | Tier | Status | Correctness | Space | Time | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| `LOAD-499-HTTP` | hard | PASS | pass | pass | pass |  |
| `LOAD-LARGE-HTTP` | hard | PASS | pass | pass | pass |  |
| `PRECONDITIONS` | preconditions | PASS | pass | pass | pass |  |

## Timing Distributions

| Timer | Count | P50 ms | P95 ms | Max ms |
| --- | ---: | ---: | ---: | ---: |
| `T_e2e` | 3 | 5404.258 | 13266.451 | 14140.028 |
| `T_http_disconnect` | 2 | 9.911 | 10.344 | 10.392 |
| `T_squash` | 2 | 125.653 | 189.731 | 196.851 |
| `T_squash_invocation_1` | 2 | 125.653 | 189.731 | 196.851 |
