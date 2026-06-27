# Implementation Prompt: Oneshot `exec_command` E2E Matrix

Implement the oneshot `exec_command` E2E coverage described in:

```text
/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os/crates/sandbox-e2e-live-test/tests/runtime/command/exec_command/oneshot/TEST_MATRIX.md
```

## Goal

Add Rust E2E tests for `sandbox-cli runtime --sandbox-id <id> exec_command`
without `--workspace-session-id`. The tests must measure both correctness and
performance. Performance evidence must include CLI latency, command timing, and
cgroup counters or an explicit cgroup-unavailable reason.

## Constraints

- Work in `/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os`.
- Use the existing `sandbox-e2e-live-test` harness only. Do not introduce a
  custom runner, mock runtime, or alternate sandbox setup.
- Keep tests black-box through `sandbox-cli`.
- Preserve the skip behavior used by existing tests:

```rust
let Some(h) = support::harness() else {
    return;
};
```

- Every `CallRecord` that should appear in `reports/<sandbox_id>/exchange.jsonl`
  must be appended with `sb.record(&rec)`. This is required for `latency_ms`
  evidence.
- The crate build script recursively includes `.rs` files under
  `tests/runtime`, so new `.rs` files under `oneshot/` are automatically mounted.

## Files to Create or Update

Create these files:

```text
crates/sandbox-e2e-live-test/tests/runtime/command/exec_command/oneshot/success_and_output.rs
crates/sandbox-e2e-live-test/tests/runtime/command/exec_command/oneshot/failure_and_validation.rs
crates/sandbox-e2e-live-test/tests/runtime/command/exec_command/oneshot/running_and_timeout.rs
crates/sandbox-e2e-live-test/tests/runtime/command/exec_command/oneshot/isolation_and_cleanup.rs
crates/sandbox-e2e-live-test/tests/runtime/command/exec_command/oneshot/cgroup_performance.rs
```

Update this existing file if needed so it records runtime calls before drop:

```text
crates/sandbox-e2e-live-test/tests/runtime/command/exec_command/one_shot.rs
```

Do not remove `TEST_MATRIX.md` or `IMPLEMENT_PROMPT.md`.

## Shared Test Shape

Follow the existing style:

```rust
use crate::support::{self, assertion as assert};

#[test]
fn descriptive_test_name() {
    let Some(h) = support::harness() else {
        return;
    };
    let (sb, _create) = h.provision_sandbox("unique-slug", None);

    let rec = h.cli().runtime(&sb.id, "exec_command", &["pwd"]);
    sb.record(&rec);

    let resp = rec.response();
    assert::ok(resp);
    assert_eq!(assert::field(resp, "/status"), "ok");
}
```

Add small local helpers inside the new files when useful, for example:

- `exec(&sb, h, args) -> CallRecord` that runs `exec_command` and records it.
- `assert_non_negative_number(resp, ptr)` for timing fields.
- `assert_no_command_session_id(resp)` for terminal cases.
- `string_field(resp, ptr)` only if it keeps assertions readable.

Keep helpers local unless duplication becomes clearly painful.

## Test Cases

Implement the matrix cases as follows.

### `success_and_output.rs`

- OS-EXEC-001: `["pwd"]`
  - Assert `status == "ok"`, `exit_code == 0`, no `command_session_id`.
  - Assert `output` contains an absolute workspace path or at least starts with `/`.
  - Assert timing fields exist and are non-negative.
- OS-EXEC-002: `["printf 'alpha\\nbeta\\n'"]`
  - Assert `status == "ok"`, `exit_code == 0`.
  - Assert output contains `alpha` before `beta`.
  - Assert `start_offset == 0`, `end_offset == total_lines`, `total_lines == 2`.
- OS-EXEC-008: bounded larger output.
  - Prefer a portable shell loop over Python if Python availability in the image
    is uncertain:

```text
sh -c 'i=0; while [ $i -lt 200 ]; do echo line-$i; i=$((i+1)); done'
```

  - Assert terminal success, monotonic offsets, nonzero token count, and either
    `total_lines == 200` or documented configured truncation.

### `failure_and_validation.rs`

- OS-EXEC-003: `["sh -c 'exit 7'"]`
  - Assert top-level success response, `status == "error"`, `exit_code == 7`.
  - Assert no terminal `command_session_id`.
- OS-EXEC-004: `["sh -c 'echo err-line >&2; exit 3'"]`
  - Assert top-level success response, `status == "error"`, `exit_code == 3`.
  - Record whether `output` includes `err-line`; do not overfit if stderr is not
    merged into the transcript by current runtime behavior.
- OS-EXEC-005: `["   "]`
  - Assert `operation_failed` with `assert::err_kind_at(&rec, "operation_failed", 1)`.
  - Assert the error message includes `cmd must be non-empty`.

### `running_and_timeout.rs`

- OS-EXEC-006: `["--yield-time-ms", "0", "sleep 2"]`
  - Assert initial `status == "running"`, `exit_code == null`, and
    `command_session_id` exists.
  - Follow up with `read_command_lines` using that id and enough wait time to
    observe terminal success.
  - Record both the exec call and the follow-up call with `sb.record(&rec)`.
- OS-EXEC-007: `["--timeout-ms", "100", "sleep 5"]`
  - Assert `status == "timed_out"`.
  - Assert `exit_code` is nonzero or otherwise matches the current configured
    timeout termination code.
  - Assert timing fields exist and command total time is finite.

### `isolation_and_cleanup.rs`

- OS-EXEC-009:
  - Run `["pwd"]` twice against the same sandbox.
  - Record both calls.
  - Assert both are terminal `ok`, both omit `command_session_id`, and both have
    independent timing data.
- OS-EXEC-012:
  - Run `["true"]`.
  - Assert terminal `ok`, `exit_code == 0`, and no `command_session_id`.
  - If a command id appears because output remains, drain it with
    `read_command_lines`; otherwise verify a fabricated command id returns a
    command-not-found style error only if the current CLI contract makes that
    assertion stable.

### `cgroup_performance.rs`

- OS-EXEC-010:
  - Run baseline `["pwd"]`.
  - Run CPU-visible command:

```text
sh -c 'i=0; while [ $i -lt 200000 ]; do i=$((i+1)); done; echo done'
```

  - Assert terminal success and `done` output.
- OS-EXEC-011:
  - Run a memory-visible command. Prefer shell/portable tools if Python is not
    guaranteed. If Python is available in the live image, this is acceptable:

```text
python3 - <<'PY'
buf = bytearray(32 * 1024 * 1024)
print(len(buf))
PY
```

  - Assert terminal success and expected output.
- Do not make cgroup counter increases hard-gating inside the Rust test unless
  the harness exposes a stable in-test artifact read. The E2E report must still
  include cgroup evidence from `observability.json`.

## Artifact and Performance Requirements

For every test that runs `exec_command`:

- Record runtime calls with `sb.record(&rec)`.
- Do not add a meaningless `rec.latency_ms >= 0` assertion; `latency_ms` is a
  `u128`. The important part is recording the call so the value is present in
  `exchange.jsonl`.
- Assert response timing fields are present and non-negative:
  - `/wall_time_seconds`
  - `/command_total_time_seconds`
- Preserve `result.json` generation by allowing `Sandbox` to drop normally.

After a live run, report for each case:

- correctness result
- `exchange.jsonl` command `latency_ms`
- response `wall_time_seconds`
- response `command_total_time_seconds`
- `observability.json.p1.available`
- `cpu_usage_usec`
- `memory_current_bytes`
- `memory_max_bytes`
- `memory_max_unlimited`
- `p1.reason` when counters are unavailable

Use this artifact query:

```bash
RUN_ROOT=<run-root>
SANDBOX_ID=<sandbox-id>

jq -c 'select(has("argv")) | {argv, exit_code, latency_ms, status: .response.status, command_exit_code: .response.exit_code, wall_time_seconds: .response.wall_time_seconds, command_total_time_seconds: .response.command_total_time_seconds}' \
  "$RUN_ROOT/reports/$SANDBOX_ID/exchange.jsonl"

jq '{sandbox_id, poll_meta, p1, latest_cgroup: .node.resources.latest.cgroup}' \
  "$RUN_ROOT/reports/$SANDBOX_ID/observability.json"
```

## Verification Workflow

1. Format the changed Rust files.
2. Run a compile-only gate first:

```bash
cargo test -p sandbox-e2e-live-test --no-run
```

3. If live Docker E2E is available, run only the runtime command surface first.
   Do not run unrelated suites while debugging.
4. Before each live E2E command, append an iteration entry to the repo's E2E
   test report with `Command`, `Good`, `Defect`, and `Fix`.
5. If a live run fails, inspect only the failed test's stdout, daemon logs, and
   scoped artifacts before changing code.

## Done Criteria

- All proposed Rust files exist and compile.
- The existing baseline test either remains valid or is updated to record its
  runtime call.
- Each matrix case OS-EXEC-001 through OS-EXEC-012 has Rust coverage or a
  clearly documented reason it is report-only.
- Runtime command records are emitted into `exchange.jsonl`.
- Live reports can show correctness, time, and cgroup evidence for the oneshot
  `exec_command` matrix.
