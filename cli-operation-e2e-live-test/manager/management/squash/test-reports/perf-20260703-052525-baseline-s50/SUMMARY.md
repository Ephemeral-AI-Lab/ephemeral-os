# LayerStack Squash Live-Docker Summary

- Run id: `perf-20260703-052525-baseline-s50`
- Generated: `2026-07-03T05:37:22+08:00`
- Pytest exit status: `0`
- Cases: `2` run · `2` pass · `0` slow · `0` fail · `0` skipped

| Case | Tier | Status | Correctness | Space | Time | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| `LOAD-COMBO-HTTP` | hard | PASS | pass | pass | pass |  |
| `PRECONDITIONS` | preconditions | PASS | pass | pass | pass |  |

## Timing Distributions

| Timer | Count | P50 ms | P95 ms | Max ms |
| --- | ---: | ---: | ---: | ---: |
| `T_e2e` | 2 | 16122.182 | 29520.123 | 31008.783 |
| `T_http_disconnect` | 1 | 20.480 | 20.480 | 20.480 |
| `T_squash` | 1 | 202.959 | 202.959 | 202.959 |
| `T_squash_invocation_1` | 1 | 192.294 | 192.294 | 192.294 |
| `T_squash_invocation_2` | 1 | 184.023 | 184.023 | 184.023 |
| `T_squash_invocation_3` | 1 | 202.959 | 202.959 | 202.959 |
| `T_squash_invocation_4` | 1 | 37.318 | 37.318 | 37.318 |
