# LayerStack Squash Live-Docker Summary

- Run id: `squash-20260703-034352`
- Generated: `2026-07-03T03:43:56+08:00`
- Pytest exit status: `1`
- Cases: `3` run · `1` pass · `0` slow · `2` fail · `0` skipped

| Case | Tier | Status | Correctness | Space | Time | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| `HTTP-01` | medium | FAIL | fail: {'status': 'error', 'exit_code': 127, 'wall_time_seconds': 0.029512667, 'command_total_time_seconds': 0.029434708, 'start_offset': 0, 'end_offset': 1, 'total_lines': 1, 'original_token_count': 12, 'output': '/bin/bash: line 1: python3: command not found'} | fail:  | fail:  |  |
| `HTTP-02` | medium | FAIL | fail: {'status': 'error', 'exit_code': 127, 'wall_time_seconds': 0.029163917, 'command_total_time_seconds': 0.029058083, 'start_offset': 0, 'end_offset': 1, 'total_lines': 1, 'original_token_count': 12, 'output': '/bin/bash: line 1: python3: command not found'} | fail:  | fail:  |  |
| `PRECONDITIONS` | preconditions | PASS | pass | pass | pass |  |

## Timing Distributions

| Timer | Count | P50 ms | P95 ms | Max ms |
| --- | ---: | ---: | ---: | ---: |
| `T_e2e` | 3 | 818.471 | 1310.641 | 1365.327 |
