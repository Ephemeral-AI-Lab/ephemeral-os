// Oneshot exec_command failure + validation: OS-EXEC-003, 004, 005.
// build.rs mounts this as module `command_exec_command_oneshot_failure_and_validation`.

use crate::support::{self, assertion as assert};
use sandbox_e2e_live_test::cli_client::CallRecord;
use sandbox_e2e_live_test::fixtures::Sandbox;
use serde_json::Value;

#[test]
fn oneshot_nonzero_exit_is_terminal_error() {
    let Some(h) = support::harness() else {
        return;
    };
    let (sb, _create) = h.provision_sandbox("oneshot-failure-exit7", None);

    let rec = exec(h, &sb, &["sh -c 'exit 7'"]);
    let resp = rec.response();

    // The transport succeeds; the command failure surfaces as a terminal status.
    assert::ok(resp);
    assert_eq!(assert::field(resp, "/status"), "error");
    assert_eq!(assert::field(resp, "/exit_code"), 7);
    assert!(
        resp.get("command_session_id").is_none(),
        "terminal failure must not leak a command_session_id: {resp}"
    );
    assert_non_negative_number(resp, "/command_total_time_seconds");
}

#[test]
fn oneshot_stderr_command_reports_terminal_metadata() {
    let Some(h) = support::harness() else {
        return;
    };
    let (sb, _create) = h.provision_sandbox("oneshot-failure-stderr", None);

    let rec = exec(h, &sb, &["sh -c 'echo err-line >&2; exit 3'"]);
    let resp = rec.response();

    assert::ok(resp);
    assert_eq!(assert::field(resp, "/status"), "error");
    assert_eq!(assert::field(resp, "/exit_code"), 3);
    // Whether stderr is merged into the transcript is runtime-defined; record it
    // for the report without overfitting either way.
    let output = assert::field(resp, "/output")
        .as_str()
        .unwrap_or_default();
    let _stderr_in_transcript = output.contains("err-line");
    assert_non_negative_number(resp, "/command_total_time_seconds");
}

#[test]
fn oneshot_blank_command_is_rejected_before_execution() {
    let Some(h) = support::harness() else {
        return;
    };
    let (sb, _create) = h.provision_sandbox("oneshot-validation-blank", None);

    let rec = exec(h, &sb, &["   "]);

    // Validation failure routes to an operation_failed error at CLI exit 1.
    assert::err_kind_at(&rec, "operation_failed", 1);
    let message = rec
        .response()
        .pointer("/error/message")
        .and_then(Value::as_str)
        .unwrap_or_default();
    assert!(
        message.contains("cmd must be non-empty"),
        "expected a non-empty cmd rejection, got {message:?}"
    );
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
