# LayerStack Squash Live-Docker Summary

- Run id: `squash-20260703-031231`
- Generated: `2026-07-03T03:13:02+08:00`
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
| `T_e2e` | 3 | 9764.205 | 17763.380 | 18652.177 |
| `T_squash` | 2 | 170.281 | 174.123 | 174.550 |
| `T_squash_invocation_1` | 2 | 170.281 | 174.123 | 174.550 |
| `T_squash_invocation_2` | 2 | 26.990 | 27.571 | 27.635 |
