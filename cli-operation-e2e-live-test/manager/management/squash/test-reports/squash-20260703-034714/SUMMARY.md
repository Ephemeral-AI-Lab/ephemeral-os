# LayerStack Squash Live-Docker Summary

- Run id: `squash-20260703-034714`
- Generated: `2026-07-03T03:47:26+08:00`
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
| `T_e2e` | 3 | 5296.187 | 5341.251 | 5346.258 |
| `T_http_disconnect` | 2 | 55.900 | 56.388 | 56.442 |
| `T_squash` | 2 | 57.900 | 59.072 | 59.202 |
| `T_squash_invocation_1` | 2 | 57.900 | 59.072 | 59.202 |
| `T_squash_invocation_2` | 1 | 29.198 | 29.198 | 29.198 |
