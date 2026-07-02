# LayerStack Squash Live-Docker Summary

- Run id: `squash-20260703-021005`
- Generated: `2026-07-03T02:10:10+08:00`
- Pytest exit status: `1`
- Cases: `3` run · `2` pass · `0` slow · `1` fail · `0` skipped

| Case | Tier | Status | Correctness | Space | Time |
| --- | --- | --- | --- | --- | --- |
| `MED-02` | medium | PASS | pass | pass | pass |
| `MED-03` | medium | FAIL | fail: {'status': 'error', 'exit_code': 1, 'wall_time_seconds': 0.047297958, 'command_total_time_seconds': 0.029887833, 'start_offset': 0, 'end_offset': 0, 'total_lines': 0, 'original_token_count': 0, 'output': ''} | fail:  | fail:  |
| `PRECONDITIONS` | preconditions | PASS | pass | pass | pass |

## Timing Distributions

| Timer | Count | P50 ms | P95 ms | Max ms |
| --- | ---: | ---: | ---: | ---: |
| `T_e2e` | 3 | 1275.046 | 1444.842 | 1463.708 |
| `T_squash` | 2 | 33.810 | 34.379 | 34.442 |
