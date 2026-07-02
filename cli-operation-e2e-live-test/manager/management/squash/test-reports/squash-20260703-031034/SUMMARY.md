# LayerStack Squash Live-Docker Summary

- Run id: `squash-20260703-031034`
- Generated: `2026-07-03T03:11:35+08:00`
- Pytest exit status: `0`
- Cases: `3` run · `3` pass · `0` slow · `0` fail · `0` skipped

| Case | Tier | Status | Correctness | Space | Time | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| `HRD-12` | hard | PASS | pass | pass | pass | `skipped:leg-b:not_constructible_at_ci_scale` |
| `MED-18` | medium | PASS | pass | pass | pass |  |
| `PRECONDITIONS` | preconditions | PASS | pass | pass | pass |  |

## Allowed Partial Skips

- `HRD-12`: `skipped:leg-b:not_constructible_at_ci_scale`

## Timing Distributions

| Timer | Count | P50 ms | P95 ms | Max ms |
| --- | ---: | ---: | ---: | ---: |
| `T_e2e` | 3 | 25375.960 | 32950.156 | 33791.733 |
| `T_squash` | 2 | 181.372 | 185.111 | 185.526 |
| `T_squash_invocation_1` | 2 | 181.372 | 185.111 | 185.526 |
| `T_squash_invocation_2` | 2 | 28.366 | 29.286 | 29.388 |
