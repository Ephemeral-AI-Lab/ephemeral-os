# Sandbox CLI Operation Timing

- Generated: `2026-07-02T08:05:23+08:00`
- Command: `/opt/homebrew/bin/pytest runtime/file/file_exec --tb=short -q --log-cli-level=WARNING`
- Exit status: `0`
- CLI calls measured: `596`
- Durations are client-side `sandbox-cli` wall time.
- `sub50` is measurement only; the suite does not enforce a timing SLO.

| Operation | Count | Min ms | P50 ms | P95 ms | Max ms | Sub50 | CLI errors |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `manager.create_sandbox` | 27 | 215.3 | 257.2 | 546.2 | 561.7 | 0.0% | 0 |
| `manager.destroy_sandbox` | 27 | 318.0 | 369.3 | 386.8 | 510.1 | 0.0% | 0 |
| `manager.list_sandboxes` | 1 | 33.7 | 33.7 | 33.7 | 33.7 | 100.0% | 0 |
| `observability.layerstack` | 50 | 25.1 | 30.0 | 40.3 | 41.0 | 100.0% | 0 |
| `observability.snapshot` | 1 | 33.9 | 33.9 | 33.9 | 33.9 | 100.0% | 0 |
| `runtime.create_workspace_session` | 7 | 29.6 | 30.1 | 32.0 | 32.1 | 100.0% | 0 |
| `runtime.destroy_workspace_session` | 8 | 33.4 | 52.6 | 58.7 | 59.5 | 25.0% | 1 |
| `runtime.exec_command` | 63 | 47.2 | 82.7 | 1071.5 | 1657.9 | 4.8% | 2 |
| `runtime.file_blame` | 30 | 21.6 | 29.0 | 32.9 | 42.9 | 100.0% | 0 |
| `runtime.file_edit` | 32 | 27.7 | 54.2 | 81.4 | 95.3 | 43.8% | 2 |
| `runtime.file_read` | 110 | 22.0 | 31.4 | 59.2 | 69.5 | 83.6% | 31 |
| `runtime.file_write` | 234 | 31.1 | 40.2 | 53.7 | 66.3 | 91.0% | 2 |
| `runtime.read_command_lines` | 1 | 43.9 | 43.9 | 43.9 | 43.9 | 100.0% | 0 |
| `runtime.write_command_stdin` | 5 | 36.8 | 69.9 | 537.3 | 538.8 | 40.0% | 0 |
