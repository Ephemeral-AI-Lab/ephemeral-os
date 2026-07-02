# LayerStack Squash Live-Docker Summary

- Run id: `squash-20260703-041820`
- Generated: `2026-07-03T04:18:42+08:00`
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
| `T_e2e` | 3 | 5429.053 | 13298.813 | 14173.231 |
| `T_http_disconnect` | 2 | 12.410 | 13.350 | 13.454 |
| `T_squash` | 2 | 135.149 | 206.551 | 214.485 |
| `T_squash_invocation_1` | 2 | 135.149 | 206.551 | 214.485 |
