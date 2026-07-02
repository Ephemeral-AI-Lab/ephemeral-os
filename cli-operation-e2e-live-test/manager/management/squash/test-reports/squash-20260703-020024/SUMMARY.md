# LayerStack Squash Live-Docker Summary

- Run id: `squash-20260703-020024`
- Generated: `2026-07-03T02:00:33+08:00`
- Pytest exit status: `1`
- Cases: `6` run · `4` pass · `0` slow · `2` fail · `0` skipped

| Case | Tier | Status | Correctness | Space | Time |
| --- | --- | --- | --- | --- | --- |
| `MED-01` | medium | PASS | pass | pass | pass |
| `MED-02` | medium | FAIL | fail: opaque xattr='' | fail:  | fail:  |
| `MED-03` | medium | FAIL | fail: {'status': 'error', 'exit_code': 1, 'wall_time_seconds': 0.047999792, 'command_total_time_seconds': 0.031582834, 'start_offset': 0, 'end_offset': 0, 'total_lines': 0, 'original_token_count': 0, 'output': ''} | fail:  | fail:  |
| `MED-11` | medium | PASS | pass | pass | pass |
| `MED-20` | medium | PASS | pass | pass | pass |
| `PRECONDITIONS` | preconditions | PASS | pass | pass | pass |

## Timing Distributions

| Timer | Count | P50 ms | P95 ms | Max ms |
| --- | ---: | ---: | ---: | ---: |
| `T_e2e` | 6 | 1248.328 | 1461.786 | 1528.394 |
| `T_quiesce` | 1 | 1.000 | 1.000 | 1.000 |
| `T_squash` | 5 | 35.114 | 47.307 | 47.862 |
