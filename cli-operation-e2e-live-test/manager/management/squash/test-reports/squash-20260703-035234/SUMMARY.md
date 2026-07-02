# LayerStack Squash Live-Docker Summary

- Run id: `squash-20260703-035234`
- Generated: `2026-07-03T03:52:49+08:00`
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
| `T_e2e` | 3 | 5261.638 | 5427.084 | 5445.467 |
| `T_http_disconnect` | 2 | 0.000 | 0.000 | 0.000 |
| `T_squash` | 2 | 54.397 | 55.374 | 55.483 |
| `T_squash_invocation_1` | 2 | 54.397 | 55.374 | 55.483 |
| `T_squash_invocation_2` | 1 | 28.564 | 28.564 | 28.564 |
