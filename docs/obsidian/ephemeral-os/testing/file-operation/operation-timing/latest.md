# Sandbox CLI Operation Timing

- Generated: `2026-07-02T07:49:03+08:00`
- Command: `/opt/homebrew/bin/pytest runtime/file/correctness --tb=short -q --log-cli-level=WARNING`
- Exit status: `0`
- CLI calls measured: `899`
- Durations are client-side `sandbox-cli` wall time.
- `sub50` is measurement only; the suite does not enforce a timing SLO.

| Operation | Count | Min ms | P50 ms | P95 ms | Max ms | Sub50 | CLI errors |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `manager.create_sandbox` | 27 | 223.0 | 261.2 | 533.1 | 551.2 | 0.0% | 0 |
| `manager.destroy_sandbox` | 27 | 316.3 | 355.6 | 398.3 | 419.2 | 0.0% | 0 |
| `manager.list_sandboxes` | 1 | 29.4 | 29.4 | 29.4 | 29.4 | 100.0% | 0 |
| `observability.layerstack` | 64 | 24.4 | 30.4 | 37.6 | 39.7 | 100.0% | 0 |
| `runtime.create_workspace_session` | 4 | 28.4 | 34.4 | 41.5 | 42.2 | 100.0% | 0 |
| `runtime.destroy_workspace_session` | 4 | 53.3 | 55.2 | 57.8 | 58.0 | 0.0% | 0 |
| `runtime.exec_command` | 24 | 51.1 | 93.2 | 1708.4 | 5645.8 | 0.0% | 0 |
| `runtime.file_blame` | 207 | 24.5 | 29.7 | 33.3 | 36.5 | 100.0% | 0 |
| `runtime.file_edit` | 61 | 34.1 | 44.2 | 47.0 | 4273.2 | 98.4% | 0 |
| `runtime.file_read` | 291 | 25.0 | 29.9 | 37.1 | 63.8 | 96.9% | 15 |
| `runtime.file_write` | 184 | 32.6 | 49.5 | 62.8 | 70.0 | 52.7% | 2 |
| `runtime.write_command_stdin` | 5 | 53.7 | 64.4 | 295.0 | 295.1 | 0.0% | 0 |
