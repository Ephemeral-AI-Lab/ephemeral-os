# LayerStack Squash Live-Docker Summary

- Run id: `squash-20260703-035709`
- Generated: `2026-07-03T03:57:21+08:00`
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
| `T_e2e` | 3 | 5245.356 | 5250.267 | 5250.813 |
| `T_http_disconnect` | 2 | 10.134 | 10.776 | 10.847 |
| `T_squash` | 2 | 52.724 | 53.006 | 53.037 |
| `T_squash_invocation_1` | 2 | 52.724 | 53.006 | 53.037 |
| `T_squash_invocation_2` | 1 | 34.314 | 34.314 | 34.314 |
