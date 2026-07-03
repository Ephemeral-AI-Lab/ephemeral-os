# LayerStack Squash Live-Docker Summary

- Run id: `squash-20260703-092637`
- Generated: `2026-07-03T09:27:05+08:00`
- Pytest exit status: `atexit`
- Cases: `11` run · `11` pass · `0` slow · `0` fail · `0` skipped

| Case | Tier | Status | Correctness | Space | Time | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| `PRECONDITIONS` | preconditions | PASS | pass | pass | pass |  |
| `SMK-01` | smoke | PASS | pass | pass | pass |  |
| `SMK-02` | smoke | PASS | pass | pass | pass |  |
| `SMK-03` | smoke | PASS | pass | pass | pass |  |
| `SMK-04` | smoke | PASS | pass | pass | pass |  |
| `SMK-05` | smoke | PASS | pass | n/a | pass |  |
| `SMK-06` | smoke | PASS | pass | pass | pass |  |
| `SMK-07` | smoke | PASS | pass | pass | pass |  |
| `SMK-08` | smoke | PASS | pass | pass | pass |  |
| `SMK-09` | smoke | PASS | pass | pass | pass |  |
| `SMK-10` | smoke | PASS | pass | pass | pass |  |

## Timing Distributions

| Timer | Count | P50 ms | P95 ms | Max ms |
| --- | ---: | ---: | ---: | ---: |
| `T_e2e` | 11 | 1313.632 | 2115.540 | 2527.341 |
| `T_quiesce` | 2 | 0.000 | 0.000 | 0.000 |
| `T_remount` | 2 | 1.000 | 1.000 | 1.000 |
| `T_squash` | 10 | 35.086 | 41.945 | 43.754 |
| `T_squash_invocation_1` | 8 | 34.898 | 42.347 | 43.754 |
| `T_squash_invocation_2` | 4 | 31.674 | 37.281 | 38.077 |
| `T_squash_invocation_3` | 1 | 26.241 | 26.241 | 26.241 |
