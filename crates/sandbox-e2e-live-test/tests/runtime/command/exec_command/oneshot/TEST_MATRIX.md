# Oneshot `exec_command` Test Matrix

Scope: `sandbox-cli runtime --sandbox-id <id> exec_command` without
`--workspace-session-id`. Each case exercises a one-shot workspace: create,
execute, report, and teardown.

## 1. List of Test Features

| Feature ID | Feature | Correctness signal | Performance and cgroup signal |
| --- | --- | --- | --- |
| F-001 | Terminal success | `status == "ok"`, `exit_code == 0`, no terminal `command_session_id` leak. | Report `exchange.jsonl` `latency_ms`, response `wall_time_seconds`, response `command_total_time_seconds`, and `observability.json.p1`. |
| F-002 | Output and transcript window accounting | `output`, `start_offset`, `end_offset`, `total_lines`, and `original_token_count` match command output. | Report timing for small and larger output cases; cgroup sample must be recorded or explicitly marked unavailable. |
| F-003 | Non-zero command exit | Process failure returns a successful transport response with terminal `status == "error"` and the process exit code. | Report command timing and latest cgroup counters/reason. |
| F-004 | Stderr-producing command | Terminal metadata is correct when the command writes to stderr. | Report whether stderr appears in transcript output and include timing/cgroup evidence. |
| F-005 | Invalid command input | Empty or whitespace command is rejected before process execution. | Report CLI latency; cgroup evidence is optional because no command should start. |
| F-006 | Running initial yield | `--yield-time-ms 0` can return `running` with `command_session_id`, then follow-up read reaches terminal state. | Report initial exec latency and follow-up read latency separately; include cgroup while command is alive if sampled. |
| F-007 | Command timeout | `--timeout-ms` maps to `status == "timed_out"` and a non-success terminal result. | Report that `command_total_time_seconds` is close to timeout plus teardown overhead; include P1 counters/reason. |
| F-008 | Sequential one-shot isolation | Repeated one-shot commands in one sandbox do not reuse stale command state. | Report per-call latency/timing and cgroup consistency. |
| F-009 | CPU cgroup visibility | CPU-heavy command completes correctly. | Compare `p1.cpu_usage_usec` with baseline when counters are available. |
| F-010 | Memory cgroup visibility | Memory-allocating command completes correctly. | Compare `p1.memory_current_bytes` with baseline when counters are available. |
| F-011 | One-shot cleanup | Terminal one-shot command leaves no active command handle. | `result.json` remains passed; report timing and P1 cgroup evidence. |
| F-012 | Report artifact completeness | Each executed case has enough data to evaluate correctness and performance. | Required artifacts: `exchange.jsonl`, `result.json`, and, when captured, `observability.json`. |

## 2. Test Cases

For every case, report:

- `cli_latency_ms`: command record `latency_ms` from `exchange.jsonl`.
- `wall_time_seconds` and `command_total_time_seconds`: response fields.
- `cgroup`: `observability.json.p1.available`, `cpu_usage_usec`,
  `memory_current_bytes`, `memory_max_bytes`, `memory_max_unlimited`, or
  `p1.reason` when unavailable.

| Case ID | Features | Command args | Correctness checks | Performance/cgroup checks |
| --- | --- | --- | --- | --- |
| OS-EXEC-001 | F-001, F-012 | `["pwd"]` | `status == "ok"`; `exit_code == 0`; no `command_session_id`; `output` contains one absolute workspace path line; `total_lines >= 1`. | Report CLI latency, wall time, command total time, and latest P1 cgroup counters/reason. |
| OS-EXEC-002 | F-002, F-012 | `["printf 'alpha\\nbeta\\n'"]` | `status == "ok"`; `exit_code == 0`; output contains `alpha` then `beta`; `start_offset == 0`; `end_offset == total_lines`; `total_lines == 2`. | Report timing and P1 cgroup counters/reason. |
| OS-EXEC-003 | F-003, F-012 | `["sh -c 'exit 7'"]` | Top-level response is ok; `status == "error"`; `exit_code == 7`; no terminal `command_session_id`. | Report timing and P1 cgroup counters/reason. |
| OS-EXEC-004 | F-004, F-012 | `["sh -c 'echo err-line >&2; exit 3'"]` | Top-level response is ok; `status == "error"`; `exit_code == 3`; record whether transcript output includes `err-line`. | Report timing and P1 cgroup counters/reason. |
| OS-EXEC-005 | F-005 | `["   "]` | Top-level error kind is `operation_failed`; message includes `cmd must be non-empty`; no command timing fields required. | Report CLI latency; cgroup sample only if `observability.json` exists. |
| OS-EXEC-006 | F-006, F-012 | `["--yield-time-ms", "0", "sleep 2"]` | Initial response is `running`; `exit_code == null`; `command_session_id` present; follow-up `read_command_lines` eventually returns `status == "ok"` and `exit_code == 0`. | Report exec latency and follow-up read latency separately; include cgroup while command is running when sampled. |
| OS-EXEC-007 | F-007, F-012 | `["--timeout-ms", "100", "sleep 5"]` | `status == "timed_out"`; exit code is non-zero or the configured timeout termination code; no running terminal handle remains. | Report timeout-adjacent command total time and P1 cgroup counters/reason. |
| OS-EXEC-008 | F-002, F-012 | `["python3 - <<'PY'\nfor i in range(200): print(f'line-{i}')\nPY"]` | `status == "ok"`; `exit_code == 0`; offsets are monotonic; `original_token_count > 0`; `total_lines == 200` or configured truncation is documented. | Compare latency with OS-EXEC-001 in the same run; report P1 cgroup counters/reason. |
| OS-EXEC-009 | F-008, F-012 | Run `["pwd"]` twice against the same sandbox. | Both calls return terminal `ok`; both omit `command_session_id`; second output is independent. | Report per-call latency/timing and cgroup consistency. |
| OS-EXEC-010 | F-009, F-012 | `["sh -c 'i=0; while [ $i -lt 200000 ]; do i=$((i+1)); done; echo done'"]` | `status == "ok"`; `exit_code == 0`; output contains `done`. | Compare `cpu_usage_usec` against OS-EXEC-001 when P1 counters are available. |
| OS-EXEC-011 | F-010, F-012 | `["python3 - <<'PY'\nbuf = bytearray(32 * 1024 * 1024)\nprint(len(buf))\nPY"]` | `status == "ok"`; `exit_code == 0`; output contains `33554432`. | Compare `memory_current_bytes` against OS-EXEC-001 when P1 counters are available. |
| OS-EXEC-012 | F-011, F-012 | Run `["true"]`, then attempt a follow-up read only if a command id appears. | Terminal response is `ok` with no id; if an id appears because output remains, follow-up read drains it; otherwise fabricated ids return command-not-found. | Report terminal timing, P1 cgroup counters/reason, and `result.json` pass status. |

## 3. Test Files

| File | Status | Coverage |
| --- | --- | --- |
| `tests/runtime/command/exec_command/one_shot.rs` | Existing Rust test | Covers OS-EXEC-001 baseline terminal success. |
| `tests/runtime/command/exec_command/oneshot/TEST_MATRIX.md` | Matrix document | Defines features, cases, required timing fields, and required cgroup fields. |
| `tests/runtime/command/exec_command/oneshot/success_and_output.rs` | Proposed Rust test file | OS-EXEC-001, OS-EXEC-002, OS-EXEC-008. |
| `tests/runtime/command/exec_command/oneshot/failure_and_validation.rs` | Proposed Rust test file | OS-EXEC-003, OS-EXEC-004, OS-EXEC-005. |
| `tests/runtime/command/exec_command/oneshot/running_and_timeout.rs` | Proposed Rust test file | OS-EXEC-006, OS-EXEC-007. |
| `tests/runtime/command/exec_command/oneshot/isolation_and_cleanup.rs` | Proposed Rust test file | OS-EXEC-009, OS-EXEC-012. |
| `tests/runtime/command/exec_command/oneshot/cgroup_performance.rs` | Proposed Rust test file | OS-EXEC-010, OS-EXEC-011. |

The crate build script recursively includes `.rs` files under `tests/runtime`,
so proposed files under `oneshot/` will be mounted automatically.

Expected run artifacts per sandbox:

| Artifact | Required data |
| --- | --- |
| `reports/<sandbox_id>/exchange.jsonl` | `argv`, command `response`, process `exit_code`, captured streams, and `latency_ms`. |
| `reports/<sandbox_id>/result.json` | Test name, sandbox id, pass/fail status, test duration, assertion counts. |
| `reports/<sandbox_id>/observability.json` | `p1.available`, cgroup CPU/memory counters or unavailable reason, poll metadata, recent traces. |

Artifact extraction:

```bash
RUN_ROOT=<run-root>
SANDBOX_ID=<sandbox-id>

jq -c 'select(has("argv")) | {argv, exit_code, latency_ms, status: .response.status, command_exit_code: .response.exit_code, wall_time_seconds: .response.wall_time_seconds, command_total_time_seconds: .response.command_total_time_seconds}' \
  "$RUN_ROOT/reports/$SANDBOX_ID/exchange.jsonl"

jq '{sandbox_id, poll_meta, p1, latest_cgroup: .node.resources.latest.cgroup, recent_traces: .node.recent_traces}' \
  "$RUN_ROOT/reports/$SANDBOX_ID/observability.json"
```
