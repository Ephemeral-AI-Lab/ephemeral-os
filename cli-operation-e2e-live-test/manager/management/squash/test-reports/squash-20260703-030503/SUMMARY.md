# LayerStack Squash Live-Docker Summary

- Run id: `squash-20260703-030503`
- Generated: `2026-07-03T03:09:09+08:00`
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
| `T_e2e` | 51 | 1697.421 | 13962.603 | 63897.091 |
| `T_quiesce` | 7 | 0.000 | 0.700 | 1.000 |
| `T_remount` | 6 | 1.000 | 1.000 | 1.000 |
| `T_squash` | 50 | 40.508 | 139.677 | 1362.959 |
| `T_squash_invocation_1` | 47 | 40.299 | 121.458 | 207.976 |
| `T_squash_invocation_10` | 1 | 42.231 | 42.231 | 42.231 |
| `T_squash_invocation_11` | 1 | 35.509 | 35.509 | 35.509 |
| `T_squash_invocation_12` | 1 | 57.772 | 57.772 | 57.772 |
| `T_squash_invocation_13` | 1 | 45.566 | 45.566 | 45.566 |
| `T_squash_invocation_14` | 1 | 32.327 | 32.327 | 32.327 |
| `T_squash_invocation_15` | 1 | 47.694 | 47.694 | 47.694 |
| `T_squash_invocation_16` | 1 | 49.101 | 49.101 | 49.101 |
| `T_squash_invocation_17` | 1 | 36.521 | 36.521 | 36.521 |
| `T_squash_invocation_18` | 1 | 34.909 | 34.909 | 34.909 |
| `T_squash_invocation_19` | 1 | 63.712 | 63.712 | 63.712 |
| `T_squash_invocation_2` | 17 | 35.362 | 42.014 | 43.297 |
| `T_squash_invocation_20` | 1 | 38.195 | 38.195 | 38.195 |
| `T_squash_invocation_21` | 1 | 34.898 | 34.898 | 34.898 |
| `T_squash_invocation_3` | 4 | 44.522 | 48.410 | 49.046 |
| `T_squash_invocation_4` | 3 | 49.606 | 50.136 | 50.195 |
| `T_squash_invocation_5` | 3 | 44.275 | 48.262 | 48.705 |
| `T_squash_invocation_6` | 3 | 38.051 | 42.035 | 42.478 |
| `T_squash_invocation_7` | 1 | 37.352 | 37.352 | 37.352 |
| `T_squash_invocation_8` | 1 | 30.093 | 30.093 | 30.093 |
| `T_squash_invocation_9` | 1 | 43.547 | 43.547 | 43.547 |
