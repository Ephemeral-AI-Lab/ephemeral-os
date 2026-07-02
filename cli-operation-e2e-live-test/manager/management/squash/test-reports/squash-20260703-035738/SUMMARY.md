# LayerStack Squash Live-Docker Summary

- Run id: `squash-20260703-035738`
- Generated: `2026-07-03T03:57:51+08:00`
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
| `T_e2e` | 3 | 5196.352 | 5291.050 | 5301.572 |
| `T_http_disconnect` | 2 | 13.139 | 16.708 | 17.104 |
| `T_squash` | 2 | 51.787 | 53.016 | 53.153 |
| `T_squash_invocation_1` | 2 | 51.787 | 53.016 | 53.153 |
| `T_squash_invocation_2` | 1 | 34.324 | 34.324 | 34.324 |
