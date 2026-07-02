# LayerStack Squash Live-Docker Summary

- Run id: `squash-20260703-031529`
- Generated: `2026-07-03T03:17:38+08:00`
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
| `T_e2e` | 51 | 1485.035 | 4645.556 | 19047.791 |
| `T_quiesce` | 7 | 0.000 | 0.700 | 1.000 |
| `T_remount` | 6 | 1.000 | 1.000 | 1.000 |
| `T_squash` | 50 | 40.080 | 135.857 | 1381.267 |
| `T_squash_invocation_1` | 47 | 38.416 | 87.003 | 171.616 |
| `T_squash_invocation_10` | 1 | 39.976 | 39.976 | 39.976 |
| `T_squash_invocation_11` | 1 | 38.674 | 38.674 | 38.674 |
| `T_squash_invocation_12` | 1 | 35.520 | 35.520 | 35.520 |
| `T_squash_invocation_13` | 1 | 47.488 | 47.488 | 47.488 |
| `T_squash_invocation_14` | 1 | 34.500 | 34.500 | 34.500 |
| `T_squash_invocation_15` | 1 | 38.202 | 38.202 | 38.202 |
| `T_squash_invocation_16` | 1 | 62.400 | 62.400 | 62.400 |
| `T_squash_invocation_17` | 1 | 41.048 | 41.048 | 41.048 |
| `T_squash_invocation_18` | 1 | 33.240 | 33.240 | 33.240 |
| `T_squash_invocation_19` | 1 | 55.514 | 55.514 | 55.514 |
| `T_squash_invocation_2` | 17 | 33.911 | 44.903 | 48.402 |
| `T_squash_invocation_20` | 1 | 32.361 | 32.361 | 32.361 |
| `T_squash_invocation_21` | 1 | 34.071 | 34.071 | 34.071 |
| `T_squash_invocation_3` | 4 | 35.531 | 38.966 | 39.387 |
| `T_squash_invocation_4` | 3 | 40.944 | 44.290 | 44.662 |
| `T_squash_invocation_5` | 3 | 40.223 | 44.066 | 44.493 |
| `T_squash_invocation_6` | 3 | 35.576 | 35.916 | 35.954 |
| `T_squash_invocation_7` | 1 | 48.718 | 48.718 | 48.718 |
| `T_squash_invocation_8` | 1 | 33.493 | 33.493 | 33.493 |
| `T_squash_invocation_9` | 1 | 40.333 | 40.333 | 40.333 |
