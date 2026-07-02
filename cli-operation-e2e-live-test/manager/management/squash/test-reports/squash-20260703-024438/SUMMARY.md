# LayerStack Squash Live-Docker Summary

- Run id: `squash-20260703-024438`
- Generated: `2026-07-03T02:48:44+08:00`
- Pytest exit status: `0`
- Cases: `51` run · `51` pass · `0` slow · `0` fail · `0` skipped

| Case | Tier | Status | Correctness | Space | Time | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| `HRD-01` | hard | PASS | pass | pass | pass |  |
| `HRD-02` | hard | PASS | pass | pass | pass |  |
| `HRD-03` | hard | PASS | pass | pass | pass |  |
| `HRD-04` | hard | PASS | pass | pass | pass |  |
| `HRD-05` | hard | PASS | pass | pass | pass |  |
| `HRD-06` | hard | PASS | pass | pass | pass |  |
| `HRD-07` | hard | PASS | pass | pass | pass |  |
| `HRD-08` | hard | PASS | pass | pass | pass |  |
| `HRD-09` | hard | PASS | pass | pass | pass |  |
| `HRD-10` | hard | PASS | pass | pass | pass |  |
| `HRD-11` | hard | PASS | pass | pass | pass |  |
| `HRD-12` | hard | PASS | pass | pass | pass | `skipped:leg-b:not_constructible_at_ci_scale` |
| `HRD-13` | hard | PASS | pass | pass | pass |  |
| `HRD-14` | hard | PASS | pass | pass | pass |  |
| `HRD-15` | hard | PASS | pass | pass | pass |  |
| `HRD-16` | hard | PASS | pass | pass | pass |  |
| `HRD-17` | hard | PASS | pass | pass | pass | `skipped:failure-leg:gate_green_env is allowed by §5.3 and unit-gated` |
| `HRD-18` | hard | PASS | pass | pass | pass |  |
| `HRD-19` | hard | PASS | pass | pass | pass |  |
| `HRD-20` | hard | PASS | pass | pass | pass |  |
| `MED-01` | medium | PASS | pass | pass | pass |  |
| `MED-02` | medium | PASS | pass | pass | pass |  |
| `MED-03` | medium | PASS | pass | pass | pass |  |
| `MED-04` | medium | PASS | pass | pass | pass |  |
| `MED-05` | medium | PASS | pass | pass | pass |  |
| `MED-06` | medium | PASS | pass | pass | pass |  |
| `MED-07` | medium | PASS | pass | pass | pass |  |
| `MED-08` | medium | PASS | pass | pass | pass |  |
| `MED-09` | medium | PASS | pass | pass | pass |  |
| `MED-10` | medium | PASS | pass | pass | pass |  |
| `MED-11` | medium | PASS | pass | pass | pass |  |
| `MED-12` | medium | PASS | pass | pass | pass |  |
| `MED-13` | medium | PASS | pass | pass | pass |  |
| `MED-14` | medium | PASS | pass | pass | pass |  |
| `MED-15` | medium | PASS | pass | pass | pass |  |
| `MED-16` | medium | PASS | pass | pass | pass |  |
| `MED-17` | medium | PASS | pass | pass | pass |  |
| `MED-18` | medium | PASS | pass | pass | pass |  |
| `MED-19` | medium | PASS | pass | pass | pass |  |
| `MED-20` | medium | PASS | pass | pass | pass |  |
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

## Allowed Partial Skips

- `HRD-12`: `skipped:leg-b:not_constructible_at_ci_scale`
- `HRD-17`: `skipped:failure-leg:gate_green_env is allowed by §5.3 and unit-gated`

## Timing Distributions

| Timer | Count | P50 ms | P95 ms | Max ms |
| --- | ---: | ---: | ---: | ---: |
| `T_e2e` | 51 | 1710.788 | 14039.032 | 65279.663 |
| `T_quiesce` | 7 | 0.000 | 0.700 | 1.000 |
| `T_remount` | 6 | 1.000 | 1.000 | 1.000 |
| `T_squash` | 50 | 40.016 | 122.128 | 1355.474 |
| `T_squash_invocation_1` | 47 | 39.477 | 97.462 | 141.832 |
| `T_squash_invocation_10` | 1 | 38.935 | 38.935 | 38.935 |
| `T_squash_invocation_11` | 1 | 40.430 | 40.430 | 40.430 |
| `T_squash_invocation_12` | 1 | 55.265 | 55.265 | 55.265 |
| `T_squash_invocation_13` | 1 | 45.766 | 45.766 | 45.766 |
| `T_squash_invocation_14` | 1 | 29.398 | 29.398 | 29.398 |
| `T_squash_invocation_15` | 1 | 40.648 | 40.648 | 40.648 |
| `T_squash_invocation_16` | 1 | 53.741 | 53.741 | 53.741 |
| `T_squash_invocation_17` | 1 | 44.085 | 44.085 | 44.085 |
| `T_squash_invocation_18` | 1 | 33.506 | 33.506 | 33.506 |
| `T_squash_invocation_19` | 1 | 51.077 | 51.077 | 51.077 |
| `T_squash_invocation_2` | 17 | 34.270 | 40.749 | 43.787 |
| `T_squash_invocation_20` | 1 | 31.989 | 31.989 | 31.989 |
| `T_squash_invocation_21` | 1 | 30.170 | 30.170 | 30.170 |
| `T_squash_invocation_3` | 4 | 40.697 | 42.524 | 42.538 |
| `T_squash_invocation_4` | 3 | 42.554 | 46.590 | 47.039 |
| `T_squash_invocation_5` | 3 | 41.347 | 42.786 | 42.946 |
| `T_squash_invocation_6` | 3 | 36.236 | 37.834 | 38.011 |
| `T_squash_invocation_7` | 1 | 41.686 | 41.686 | 41.686 |
| `T_squash_invocation_8` | 1 | 31.933 | 31.933 | 31.933 |
| `T_squash_invocation_9` | 1 | 37.088 | 37.088 | 37.088 |
