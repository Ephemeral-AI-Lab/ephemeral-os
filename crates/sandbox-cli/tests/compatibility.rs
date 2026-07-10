#![cfg(all(feature = "manager", feature = "runtime", feature = "observability"))]

use std::process::Command;

use sandbox_protocol::catalog_to_value;
use serde_json::{json, Value};

#[test]
fn all_feature_compatibility_catalog_matches_phase_zero_fixture() {
    let catalog = json!({
        "management": catalog_to_value(sandbox_manager_operations::manager_catalog()),
        "runtime": catalog_to_value(sandbox_runtime_operations::runtime_catalog()),
        "observability": catalog_to_value(
            sandbox_observability_operations::observability_catalog()
        ),
    });
    let fixture = include_str!("fixtures/compatibility-catalog.json");
    let fixture = fixture.strip_suffix('\n').unwrap_or(fixture);

    assert_eq!(catalog.to_string(), fixture);
}

#[test]
fn unknown_operation_errors_and_exit_codes_match_phase_zero_fixture() {
    let cases = [
        (
            "sandbox-manager-cli",
            env!("CARGO_BIN_EXE_sandbox-manager-cli"),
            vec![
                "--gateway-socket",
                "127.0.0.1:1",
                "--gateway-auth-token",
                "phase-0-fixture",
                "phase0_unknown_operation",
            ],
        ),
        (
            "sandbox-runtime-cli",
            env!("CARGO_BIN_EXE_sandbox-runtime-cli"),
            vec![
                "--gateway-socket",
                "127.0.0.1:1",
                "--gateway-auth-token",
                "phase-0-fixture",
                "--sandbox-id",
                "eos-phase-0",
                "phase0_unknown_operation",
            ],
        ),
        (
            "sandbox-observability-cli",
            env!("CARGO_BIN_EXE_sandbox-observability-cli"),
            vec![
                "--gateway-socket",
                "127.0.0.1:1",
                "--gateway-auth-token",
                "phase-0-fixture",
                "phase0_unknown_operation",
            ],
        ),
    ];
    let actual = cases
        .iter()
        .map(|(name, binary, argv)| invocation(name, binary, argv))
        .collect::<Vec<_>>();

    assert_eq!(
        format!("{}\n", Value::Array(actual)),
        include_str!("fixtures/unknown-operation-errors.json")
    );
}

fn invocation(name: &str, binary: &str, argv: &[&str]) -> Value {
    let output = Command::new(binary)
        .args(argv)
        .output()
        .expect("run sandbox CLI");

    json!({
        "binary": name,
        "argv": argv,
        "exit_code": output.status.code().expect("numeric exit code"),
        "stdout": String::from_utf8(output.stdout).expect("stdout utf8"),
        "stderr": String::from_utf8(output.stderr).expect("stderr utf8"),
    })
}
