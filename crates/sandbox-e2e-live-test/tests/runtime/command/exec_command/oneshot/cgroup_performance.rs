// Oneshot exec_command cgroup/perf visibility: OS-EXEC-010, 011. cgroup counter
// comparisons are report-only (read from observability.json), not hard-gated here.
// build.rs mounts this as module `command_exec_command_oneshot_cgroup_performance`.

use crate::support::{self, assertion as assert};
use sandbox_e2e_live_test::cli_client::CallRecord;
use sandbox_e2e_live_test::fixtures::Sandbox;
use serde_json::Value;

#[test]
fn cpu_heavy_command_completes_with_baseline() {
    let Some(h) = support::harness() else {
        return;
    };
    let (sb, _create) = h.provision_sandbox("oneshot-cgroup-cpu", None);

    // Baseline in the same sandbox so the report can compare cpu_usage_usec.
    let baseline = exec(h, &sb, &["pwd"]);
    assert::ok(baseline.response());

    let rec = exec(
        h,
        &sb,
        &["sh -c 'i=0; while [ $i -lt 200000 ]; do i=$((i+1)); done; echo done'"],
    );
    let resp = rec.response();

    assert::ok(resp);
    assert_eq!(assert::field(resp, "/status"), "ok");
    assert_eq!(assert::field(resp, "/exit_code"), 0);
    assert!(
        string_field(resp, "/output").contains("done"),
        "expected CPU loop to print done: {resp}"
    );
    assert_non_negative_number(resp, "/command_total_time_seconds");
}

#[test]
fn memory_command_completes_with_baseline() {
    let Some(h) = support::harness() else {
        return;
    };
    let (sb, _create) = h.provision_sandbox("oneshot-cgroup-mem", None);

    let baseline = exec(h, &sb, &["pwd"]);
    assert::ok(baseline.response());

    // Portable 32 MiB pass; the output
    // byte count proves completion while the report reads memory_current_bytes.
    let rec = exec(h, &sb, &["sh -c 'head -c 33554432 /dev/zero | wc -c'"]);
    let resp = rec.response();

    assert::ok(resp);
    assert_eq!(assert::field(resp, "/status"), "ok");
    assert_eq!(assert::field(resp, "/exit_code"), 0);
    assert!(
        string_field(resp, "/output").contains("33554432"),
        "expected 32 MiB byte count in output: {resp}"
    );
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

fn string_field<'a>(resp: &'a Value, ptr: &str) -> &'a str {
    assert::field(resp, ptr)
        .as_str()
        .unwrap_or_else(|| panic!("{ptr} is not a string: {resp}"))
}
