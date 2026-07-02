# LayerStack Squash Live-Docker Summary

- Run id: `squash-20260703-031452`
- Generated: `2026-07-03T03:15:22+08:00`
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
| `T_e2e` | 3 | 9780.164 | 17801.240 | 18692.471 |
| `T_squash` | 2 | 165.247 | 168.220 | 168.550 |
| `T_squash_invocation_1` | 2 | 165.247 | 168.220 | 168.550 |
| `T_squash_invocation_2` | 2 | 25.433 | 26.351 | 26.453 |
