# LayerStack Squash Live-Docker Summary

- Run id: `squash-20260703-034627`
- Generated: `2026-07-03T03:46:34+08:00`
- Pytest exit status: `1`
- Cases: `3` run · `1` pass · `0` slow · `2` fail · `0` skipped

| Case | Tier | Status | Correctness | Space | Time | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| `HTTP-01` | medium | FAIL | fail: {'manifest_version': 5, 'squashed_blocks': [{'squashed_layer_id': 'S000005-00000010', 'replaced_layer_ids': ['L000003-00000004', 'L000002-00000000'], 'replaced_layers': 'leased', 'blocked_reasons': ['pinned:mapped_file_pinned_workspace']}]} | fail:  | fail:  |  |
| `HTTP-02` | medium | FAIL | fail: {'status': 'ok', 'exit_code': 0, 'wall_time_seconds': 0.029953791, 'command_total_time_seconds': 0.029724041, 'start_offset': 0, 'end_offset': 147, 'total_lines': 147, 'original_token_count': 256, 'output': 'http-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nhttp-0\nht'} | fail:  | fail:  |  |
| `PRECONDITIONS` | preconditions | PASS | pass | pass | pass |  |

## Timing Distributions

| Timer | Count | P50 ms | P95 ms | Max ms |
| --- | ---: | ---: | ---: | ---: |
| `T_e2e` | 3 | 2467.373 | 2574.035 | 2585.886 |
| `T_squash` | 2 | 46.385 | 48.310 | 48.524 |
| `T_squash_invocation_1` | 2 | 46.385 | 48.310 | 48.524 |
