# LayerStack Squash Live-Docker Summary

- Run id: `squash-20260703-024850`
- Generated: `2026-07-03T02:52:56+08:00`
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
| `T_e2e` | 51 | 1676.103 | 14136.297 | 65334.935 |
| `T_quiesce` | 7 | 0.000 | 0.700 | 1.000 |
| `T_remount` | 6 | 1.000 | 1.000 | 1.000 |
| `T_squash` | 50 | 39.337 | 152.138 | 1340.710 |
| `T_squash_invocation_1` | 47 | 39.262 | 129.893 | 259.698 |
| `T_squash_invocation_10` | 1 | 47.536 | 47.536 | 47.536 |
| `T_squash_invocation_11` | 1 | 41.193 | 41.193 | 41.193 |
| `T_squash_invocation_12` | 1 | 43.187 | 43.187 | 43.187 |
| `T_squash_invocation_13` | 1 | 50.101 | 50.101 | 50.101 |
| `T_squash_invocation_14` | 1 | 34.014 | 34.014 | 34.014 |
| `T_squash_invocation_15` | 1 | 46.678 | 46.678 | 46.678 |
| `T_squash_invocation_16` | 1 | 50.528 | 50.528 | 50.528 |
| `T_squash_invocation_17` | 1 | 40.984 | 40.984 | 40.984 |
| `T_squash_invocation_18` | 1 | 33.932 | 33.932 | 33.932 |
| `T_squash_invocation_19` | 1 | 47.768 | 47.768 | 47.768 |
| `T_squash_invocation_2` | 17 | 34.637 | 45.860 | 46.521 |
| `T_squash_invocation_20` | 1 | 33.098 | 33.098 | 33.098 |
| `T_squash_invocation_21` | 1 | 34.402 | 34.402 | 34.402 |
| `T_squash_invocation_3` | 4 | 44.764 | 46.192 | 46.284 |
| `T_squash_invocation_4` | 3 | 44.300 | 49.113 | 49.648 |
| `T_squash_invocation_5` | 3 | 47.298 | 47.581 | 47.612 |
| `T_squash_invocation_6` | 3 | 37.464 | 39.259 | 39.458 |
| `T_squash_invocation_7` | 1 | 39.815 | 39.815 | 39.815 |
| `T_squash_invocation_8` | 1 | 36.819 | 36.819 | 36.819 |
| `T_squash_invocation_9` | 1 | 40.950 | 40.950 | 40.950 |
