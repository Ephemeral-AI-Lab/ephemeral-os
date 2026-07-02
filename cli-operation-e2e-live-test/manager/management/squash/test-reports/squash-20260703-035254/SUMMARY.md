# LayerStack Squash Live-Docker Summary

- Run id: `squash-20260703-035254`
- Generated: `2026-07-03T03:53:07+08:00`
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
| `T_e2e` | 3 | 5233.970 | 5248.048 | 5249.612 |
| `T_http_disconnect` | 2 | 0.000 | 0.000 | 0.000 |
| `T_squash` | 2 | 55.159 | 55.329 | 55.348 |
| `T_squash_invocation_1` | 2 | 55.159 | 55.329 | 55.348 |
| `T_squash_invocation_2` | 1 | 29.767 | 29.767 | 29.767 |
