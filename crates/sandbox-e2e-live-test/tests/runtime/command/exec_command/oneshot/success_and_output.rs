// Oneshot exec_command success + output windowing: OS-EXEC-001, 002, 008.
// build.rs mounts this as module `command_exec_command_oneshot_success_and_output`.

use crate::support::{self, assertion as assert};
use sandbox_e2e_live_test::cli_client::CallRecord;
use sandbox_e2e_live_test::fixtures::Sandbox;
use serde_json::Value;

#[test]
fn oneshot_pwd_returns_ok_with_absolute_output() {
    let Some(h) = support::harness() else {
        return;
    };
    let (sb, _create) = h.provision_sandbox("oneshot-success-pwd", None);

    let rec = exec(h, &sb, &["pwd"]);
    let resp = rec.response();

    assert::ok(resp);
    assert_eq!(assert::field(resp, "/status"), "ok");
    assert_eq!(assert::field(resp, "/exit_code"), 0);
    assert_no_command_session_id(resp);
    let output = string_field(resp, "/output");
    assert!(
        output.trim_start().starts_with('/'),
        "expected an absolute workspace path, got {output:?}"
    );
    assert!(u64_field(resp, "/total_lines") >= 1);
    assert_non_negative_number(resp, "/wall_time_seconds");
    assert_non_negative_number(resp, "/command_total_time_seconds");
}

#[test]
fn oneshot_printf_two_lines_windows_offsets() {
    let Some(h) = support::harness() else {
        return;
    };
    let (sb, _create) = h.provision_sandbox("oneshot-success-printf", None);

    let rec = exec(h, &sb, &["printf 'alpha\\nbeta\\n'"]);
    let resp = rec.response();

    assert::ok(resp);
    assert_eq!(assert::field(resp, "/status"), "ok");
    assert_eq!(assert::field(resp, "/exit_code"), 0);
    let output = string_field(resp, "/output");
    let alpha = output.find("alpha").expect("output contains alpha");
    let beta = output.find("beta").expect("output contains beta");
    assert!(alpha < beta, "alpha should precede beta: {output:?}");
    assert_eq!(assert::field(resp, "/start_offset"), 0);
    assert_eq!(assert::field(resp, "/total_lines"), 2);
    assert_eq!(
        assert::field(resp, "/end_offset"),
        assert::field(resp, "/total_lines")
    );
    assert_non_negative_number(resp, "/wall_time_seconds");
    assert_non_negative_number(resp, "/command_total_time_seconds");
}

#[test]
fn oneshot_bounded_larger_output_has_monotonic_offsets() {
    let Some(h) = support::harness() else {
        return;
    };
    let (sb, _create) = h.provision_sandbox("oneshot-success-bulk", None);

    // Portable shell loop (no Python dependency); emits exactly 200 lines.
    let rec = exec(
        h,
        &sb,
        &["sh -c 'i=0; while [ $i -lt 200 ]; do echo line-$i; i=$((i+1)); done'"],
    );
    let resp = rec.response();

    assert::ok(resp);
    assert_eq!(assert::field(resp, "/status"), "ok");
    assert_eq!(assert::field(resp, "/exit_code"), 0);
    let start = u64_field(resp, "/start_offset");
    let end = u64_field(resp, "/end_offset");
    assert!(start <= end, "offsets should be monotonic: {start} > {end}");
    assert!(u64_field(resp, "/original_token_count") > 0);
    // total_lines is the full line count, independent of any output-window cap.
    assert_eq!(assert::field(resp, "/total_lines"), 200);
    assert_non_negative_number(resp, "/wall_time_seconds");
    assert_non_negative_number(resp, "/command_total_time_seconds");
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
        "unexpected terminal command_session_id: {resp}"
    );
}

fn string_field<'a>(resp: &'a Value, ptr: &str) -> &'a str {
    assert::field(resp, ptr)
        .as_str()
        .unwrap_or_else(|| panic!("{ptr} is not a string: {resp}"))
}

fn u64_field(resp: &Value, ptr: &str) -> u64 {
    assert::field(resp, ptr)
        .as_u64()
        .unwrap_or_else(|| panic!("{ptr} is not an unsigned integer: {resp}"))
}
