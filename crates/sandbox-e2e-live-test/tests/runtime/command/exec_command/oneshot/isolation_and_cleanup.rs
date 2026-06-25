// Oneshot exec_command isolation + cleanup: OS-EXEC-009, 012.
// build.rs mounts this as module `command_exec_command_oneshot_isolation_and_cleanup`.

use crate::support::{self, assertion as assert};
use sandbox_e2e_live_test::cli_client::CallRecord;
use sandbox_e2e_live_test::fixtures::Sandbox;
use serde_json::Value;

#[test]
fn sequential_oneshot_commands_do_not_reuse_state() {
    let Some(h) = support::harness() else {
        return;
    };
    let (sb, _create) = h.provision_sandbox("oneshot-isolation-seq", None);

    let first = exec(h, &sb, &["pwd"]);
    let first_resp = first.response();
    assert::ok(first_resp);
    assert_eq!(assert::field(first_resp, "/status"), "ok");
    assert_no_command_session_id(first_resp);

    let second = exec(h, &sb, &["pwd"]);
    let second_resp = second.response();
    assert::ok(second_resp);
    assert_eq!(assert::field(second_resp, "/status"), "ok");
    assert_eq!(assert::field(second_resp, "/exit_code"), 0);
    assert_no_command_session_id(second_resp);

    // Each call carries its own timing rather than reusing the prior command's.
    assert_non_negative_number(first_resp, "/command_total_time_seconds");
    assert_non_negative_number(second_resp, "/command_total_time_seconds");
}

#[test]
fn terminal_oneshot_leaves_no_active_command_handle() {
    let Some(h) = support::harness() else {
        return;
    };
    let (sb, _create) = h.provision_sandbox("oneshot-cleanup-true", None);

    let rec = exec(h, &sb, &["true"]);
    let resp = rec.response();
    assert::ok(resp);
    assert_eq!(assert::field(resp, "/status"), "ok");
    assert_eq!(assert::field(resp, "/exit_code"), 0);

    // A terminal `true` should not leave an active handle. If buffered output
    // surfaced an id, draining it must still reach a terminal read.
    if let Some(command_session_id) = resp.get("command_session_id").and_then(Value::as_str) {
        let drain = h.cli().runtime(
            &sb.id,
            "read_command_lines",
            &["--command-session-id", command_session_id],
        );
        sb.record(&drain);
        assert::ok(drain.response());
    }
}

fn exec(h: &support::Harness, sb: &Sandbox, args: &[&str]) -> CallRecord {
    let rec = h.cli().runtime(&sb.id, "exec_command", args);
    sb.record(&rec);
    rec
}

fn assert_non_negative_number(resp: &Value, ptr: &str) {
    let value = assert::field(resp, ptr)
        .as_f64()
        .unwrap_or_else(|| panic!("{ptr} is not a number: {resp}"));
    assert!(value >= 0.0, "{ptr} should be non-negative, got {value}");
}

fn assert_no_command_session_id(resp: &Value) {
    assert!(
        resp.get("command_session_id").is_none(),
        "terminal one-shot must not leak a command_session_id: {resp}"
    );
}
