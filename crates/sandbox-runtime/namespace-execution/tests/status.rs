use sandbox_runtime_namespace_execution::{NamespaceExecutionTerminalStatus, RunnerOutcome};
use sandbox_runtime_namespace_process::runner::protocol::RunResult;
use serde_json::json;

#[test]
fn as_str_strings_match_the_wire_vocabulary() {
    assert_eq!(NamespaceExecutionTerminalStatus::Ok.as_str(), "ok");
    assert_eq!(NamespaceExecutionTerminalStatus::Error.as_str(), "error");
    assert_eq!(
        NamespaceExecutionTerminalStatus::TimedOut.as_str(),
        "timed_out"
    );
    assert_eq!(
        NamespaceExecutionTerminalStatus::Cancelled.as_str(),
        "cancelled"
    );
}

#[test]
fn status_projects_the_payload_status_string() {
    assert_eq!(
        outcome(run_result(0, "ok")).status(),
        NamespaceExecutionTerminalStatus::Ok
    );
    assert_eq!(
        outcome(run_result(1, "error")).status(),
        NamespaceExecutionTerminalStatus::Error
    );
    assert_eq!(
        outcome(run_result(0, "timed_out")).status(),
        NamespaceExecutionTerminalStatus::TimedOut
    );
    assert_eq!(
        outcome(run_result(0, "cancelled")).status(),
        NamespaceExecutionTerminalStatus::Cancelled
    );
}

#[test]
fn status_defaults_to_error_when_absent_or_unknown() {
    assert_eq!(
        outcome(run_result_without_status(1)).status(),
        NamespaceExecutionTerminalStatus::Error
    );
    assert_eq!(
        outcome(run_result(0, "bogus")).status(),
        NamespaceExecutionTerminalStatus::Error
    );
}

#[test]
fn status_defaults_to_error_for_non_string_or_non_object_payloads() {
    assert_eq!(
        outcome(run_result_payload(1, json!({ "status": 7 }))).status(),
        NamespaceExecutionTerminalStatus::Error
    );
    assert_eq!(
        outcome(run_result_payload(1, json!(42))).status(),
        NamespaceExecutionTerminalStatus::Error
    );
}

#[test]
fn cancelled_outcome_overrides_status_and_exit_code() {
    // Even when the wire result reports a clean exit, a cancelled execution
    // reports Cancelled/130 (the engine knows the cancel; the wire does not).
    let outcome = RunnerOutcome::new(run_result(0, "ok")).with_cancelled(true);
    assert_eq!(
        outcome.status(),
        NamespaceExecutionTerminalStatus::Cancelled
    );
    assert_eq!(outcome.exit_code(), 130);
}

#[test]
fn payload_exposes_the_raw_value() {
    assert_eq!(
        outcome(run_result(0, "ok")).payload().to_string(),
        r#"{"status":"ok"}"#
    );
}

fn run_result(exit_code: i32, status: &str) -> RunResult {
    RunResult {
        exit_code,
        payload: serde_json::json!({ "status": status }),
    }
}

fn run_result_without_status(exit_code: i32) -> RunResult {
    RunResult {
        exit_code,
        payload: serde_json::json!({}),
    }
}

fn run_result_payload(exit_code: i32, payload: serde_json::Value) -> RunResult {
    RunResult { exit_code, payload }
}

fn outcome(result: RunResult) -> RunnerOutcome {
    RunnerOutcome::new(result)
}
