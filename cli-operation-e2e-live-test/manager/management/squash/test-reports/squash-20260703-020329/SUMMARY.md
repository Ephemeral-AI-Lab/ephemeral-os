# LayerStack Squash Live-Docker Summary

- Run id: `squash-20260703-020329`
- Generated: `2026-07-03T02:03:33+08:00`
- Pytest exit status: `1`
- Cases: `3` run · `1` pass · `0` slow · `2` fail · `0` skipped

| Case | Tier | Status | Correctness | Space | Time |
| --- | --- | --- | --- | --- | --- |
| `MED-02` | medium | FAIL | fail: opaque xattr='' | fail:  | fail:  |
| `MED-03` | medium | FAIL | fail: {'status': 'error', 'exit_code': 1, 'wall_time_seconds': 0.055898833, 'command_total_time_seconds': 0.037760417, 'start_offset': 0, 'end_offset': 0, 'total_lines': 0, 'original_token_count': 0, 'output': ''} | fail:  | fail:  |
| `PRECONDITIONS` | preconditions | PASS | pass | pass | pass |

## Timing Distributions

| Timer | Count | P50 ms | P95 ms | Max ms |
| --- | ---: | ---: | ---: | ---: |
| `T_e2e` | 3 | 1242.204 | 1274.468 | 1278.053 |
| `T_squash` | 2 | 33.963 | 34.468 | 34.524 |
