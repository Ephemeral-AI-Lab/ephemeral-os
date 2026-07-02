# LayerStack Squash Live-Docker Summary

- Run id: `squash-20260703-031857`
- Generated: `2026-07-03T03:19:27+08:00`
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
| `T_e2e` | 3 | 10001.333 | 17110.657 | 17900.582 |
| `T_squash` | 2 | 175.653 | 182.811 | 183.606 |
| `T_squash_invocation_1` | 2 | 175.653 | 182.811 | 183.606 |
| `T_squash_invocation_2` | 2 | 27.314 | 28.196 | 28.294 |
