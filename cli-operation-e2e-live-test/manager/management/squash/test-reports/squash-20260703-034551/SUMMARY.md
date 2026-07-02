# LayerStack Squash Live-Docker Summary

- Run id: `squash-20260703-034551`
- Generated: `2026-07-03T03:45:57+08:00`
- Pytest exit status: `1`
- Cases: `3` run · `1` pass · `0` slow · `2` fail · `0` skipped

| Case | Tier | Status | Correctness | Space | Time | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| `HTTP-01` | medium | FAIL | fail: {'status': 'error', 'exit_code': 1, 'wall_time_seconds': 0.041584083, 'command_total_time_seconds': 0.025524666, 'start_offset': 0, 'end_offset': 4, 'total_lines': 4, 'original_token_count': 48, 'output': "layers_bytes du: cannot access '/eos/layer-stack/layers': No such file or directory\nstaging_entries 0\nremount_residue 0\nlayer_dirs find: '/eos/layer-stack/layers': No such file or directory"} | fail:  | fail:  |  |
| `HTTP-02` | medium | FAIL | fail: {'status': 'error', 'exit_code': 1, 'wall_time_seconds': 0.0499635, 'command_total_time_seconds': 0.031003625, 'start_offset': 0, 'end_offset': 4, 'total_lines': 4, 'original_token_count': 48, 'output': "layers_bytes du: cannot access '/eos/layer-stack/layers': No such file or directory\nstaging_entries 0\nremount_residue 0\nlayer_dirs find: '/eos/layer-stack/layers': No such file or directory"} | fail:  | fail:  |  |
| `PRECONDITIONS` | preconditions | PASS | pass | pass | pass |  |

## Timing Distributions

| Timer | Count | P50 ms | P95 ms | Max ms |
| --- | ---: | ---: | ---: | ---: |
| `T_e2e` | 3 | 1935.321 | 1947.442 | 1948.789 |
