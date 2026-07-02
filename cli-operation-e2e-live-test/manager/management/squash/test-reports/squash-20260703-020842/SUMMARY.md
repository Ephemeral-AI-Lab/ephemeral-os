# LayerStack Squash Live-Docker Summary

- Run id: `squash-20260703-020842`
- Generated: `2026-07-03T02:08:48+08:00`
- Pytest exit status: `1`
- Cases: `3` run · `1` pass · `0` slow · `2` fail · `0` skipped

| Case | Tier | Status | Correctness | Space | Time |
| --- | --- | --- | --- | --- | --- |
| `MED-02` | medium | FAIL | fail: {'status': 'ok', 'exit_code': 0, 'wall_time_seconds': 0.047911375, 'command_total_time_seconds': 0.031867833, 'start_offset': 0, 'end_offset': 2, 'total_lines': 2, 'original_token_count': 4, 'output': '.wh..wh..opq\nnew'} | fail:  | fail:  |
| `MED-03` | medium | FAIL | fail: {'status': 'error', 'exit_code': 1, 'wall_time_seconds': 0.047414167, 'command_total_time_seconds': 0.029698458, 'start_offset': 0, 'end_offset': 0, 'total_lines': 0, 'original_token_count': 0, 'output': ''} | fail:  | fail:  |
| `PRECONDITIONS` | preconditions | PASS | pass | pass | pass |

## Timing Distributions

| Timer | Count | P50 ms | P95 ms | Max ms |
| --- | ---: | ---: | ---: | ---: |
| `T_e2e` | 3 | 1261.303 | 1421.816 | 1439.651 |
| `T_squash` | 2 | 35.079 | 35.597 | 35.655 |
