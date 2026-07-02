# LayerStack Squash Live-Docker Summary

- Run id: `squash-20260703-040142`
- Generated: `2026-07-03T04:01:58+08:00`
- Pytest exit status: `1`
- Cases: `3` run · `2` pass · `0` slow · `1` fail · `0` skipped

| Case | Tier | Status | Correctness | Space | Time | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| `LOAD-499` | hard | PASS | pass | pass | pass |  |
| `LOAD-LARGE` | hard | FAIL | fail: {'status': 'error', 'exit_code': 1, 'wall_time_seconds': 0.025891001, 'command_total_time_seconds': 0.025823626, 'start_offset': 0, 'end_offset': 1, 'total_lines': 1, 'original_token_count': 17, 'output': '/bin/bash: line 1: data/large-blob.txt: No such file or directory'} | fail:  | fail:  |  |
| `PRECONDITIONS` | preconditions | PASS | pass | pass | pass |  |

## Timing Distributions

| Timer | Count | P50 ms | P95 ms | Max ms |
| --- | ---: | ---: | ---: | ---: |
| `T_e2e` | 3 | 1181.694 | 8836.611 | 9687.157 |
| `T_squash` | 2 | 100.074 | 157.807 | 164.222 |
| `T_squash_invocation_1` | 2 | 100.074 | 157.807 | 164.222 |
