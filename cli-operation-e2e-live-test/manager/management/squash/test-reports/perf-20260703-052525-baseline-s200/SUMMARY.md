# LayerStack Squash Live-Docker Summary

- Run id: `perf-20260703-052525-baseline-s200`
- Generated: `2026-07-03T05:42:55+08:00`
- Pytest exit status: `0`
- Cases: `2` run · `2` pass · `0` slow · `0` fail · `0` skipped

| Case | Tier | Status | Correctness | Space | Time | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| `LOAD-COMBO-HTTP` | hard | PASS | pass | pass | pass |  |
| `PRECONDITIONS` | preconditions | PASS | pass | pass | pass |  |

## Timing Distributions

| Timer | Count | P50 ms | P95 ms | Max ms |
| --- | ---: | ---: | ---: | ---: |
| `T_e2e` | 2 | 24919.176 | 46236.328 | 48604.900 |
| `T_http_disconnect` | 1 | 21.475 | 21.475 | 21.475 |
| `T_squash` | 1 | 2141.280 | 2141.280 | 2141.280 |
| `T_squash_invocation_1` | 1 | 648.308 | 648.308 | 648.308 |
| `T_squash_invocation_2` | 1 | 2141.280 | 2141.280 | 2141.280 |
| `T_squash_invocation_3` | 1 | 802.918 | 802.918 | 802.918 |
| `T_squash_invocation_4` | 1 | 35.442 | 35.442 | 35.442 |
