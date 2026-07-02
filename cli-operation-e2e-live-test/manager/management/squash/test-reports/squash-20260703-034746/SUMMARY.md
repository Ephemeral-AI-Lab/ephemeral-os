# LayerStack Squash Live-Docker Summary

- Run id: `squash-20260703-034746`
- Generated: `2026-07-03T03:47:59+08:00`
- Pytest exit status: `0`
- Cases: `3` run · `3` pass · `0` slow · `0` fail · `0` skipped

| Case | Tier | Status | Correctness | Space | Time | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| `HTTP-01` | medium | PASS | pass | pass | pass |  |
| `HTTP-02` | medium | PASS | pass | pass | pass |  |
| `PRECONDITIONS` | preconditions | PASS | pass | pass | pass |  |

## Timing Distributions

| Timer | Count | P50 ms | P95 ms | Max ms |
| --- | ---: | ---: | ---: | ---: |
| `T_e2e` | 3 | 5212.221 | 5362.210 | 5378.875 |
| `T_http_disconnect` | 2 | 55.300 | 55.709 | 55.754 |
| `T_squash` | 2 | 64.163 | 66.168 | 66.391 |
| `T_squash_invocation_1` | 2 | 64.163 | 66.168 | 66.391 |
| `T_squash_invocation_2` | 1 | 32.088 | 32.088 | 32.088 |
