// Oneshot exec_command running + timeout: OS-EXEC-006, 007.
// build.rs mounts this as module `command_exec_command_oneshot_running_and_timeout`.

use std::time::Duration;

use crate::support::{self, assertion as assert};
use sandbox_e2e_live_test::cli_client::CallRecord;
use sandbox_e2e_live_test::fixtures::Sandbox;
use serde_json::Value;

#[test]
fn oneshot_yield_zero_returns_running_then_terminal() {
    let Some(h) = support::harness() else {
        return;
    };
    let (sb, _create) = h.provision_sandbox("oneshot-running-yield", None);

    let rec = exec(h, &sb, &["--yield-time-ms", "0", "sleep 2"]);
    let resp = rec.response();

    assert::ok(resp);
    assert_eq!(assert::field(resp, "/status"), "running");
    assert!(
        assert::field(resp, "/exit_code").is_null(),
        "a running command has a null exit_code: {resp}"
    );
    let command_session_id = assert::field(resp, "/command_session_id")
        .as_str()
        .expect("running response carries a command_session_id")
        .to_owned();

    let terminal = read_until_terminal(h, &sb, &command_session_id);
    assert::ok(&terminal);
    assert_eq!(assert::field(&terminal, "/status"), "ok");
    assert_eq!(assert::field(&terminal, "/exit_code"), 0);
}

#[test]
fn oneshot_timeout_maps_to_timed_out() {
    let Some(h) = support::harness() else {
        return;
    };
    let (sb, _create) = h.provision_sandbox("oneshot-timeout", None);

    let rec = exec(h, &sb, &["--timeout-ms", "100", "sleep 5"]);
    let resp = rec.response();

    assert::ok(resp);
    assert_eq!(assert::field(resp, "/status"), "timed_out");
    // The timeout termination code is runtime-defined; require only that it is
    // not a successful zero exit.
    let exit_code = assert::field(resp, "/exit_code");
    assert!(
        exit_code.is_null() || exit_code.as_i64() != Some(0),
        "timed_out must not report a successful exit: {resp}"
    );
    assert_non_negative_number(resp, "/command_total_time_seconds");
}

fn read_until_terminal(h: &support::Harness, sb: &Sandbox, command_session_id: &str) -> Value {
    for _ in 0..40 {
        let rec = h.cli().runtime(
            &sb.id,
            "read_command_lines",
            &["--command-session-id", command_session_id],
        );
        sb.record(&rec);
        let resp = rec.response();
        let status = resp
            .pointer("/status")
            .and_then(Value::as_str)
            .unwrap_or_default();
        if status != "running" {
            return resp.clone();
        }
        std::thread::sleep(Duration::from_millis(250));
    }
    panic!("command {command_session_id} did not reach a terminal state in time");
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
