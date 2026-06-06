#![allow(clippy::unwrap_used)]
use super::*;

#[test]
fn provider_kind_serializes_to_docker() {
    assert_eq!(
        serde_json::to_value(ProviderKind::Docker).unwrap(),
        serde_json::json!("docker")
    );
    assert_eq!(ProviderKind::Docker.as_str(), "docker");
}

#[test]
fn create_spec_defaults_language_to_python() {
    let spec = CreateSandboxSpec::default();
    assert_eq!(spec.language, "python");
    // serde default also fills language when absent.
    let parsed: CreateSandboxSpec = serde_json::from_value(serde_json::json!({
        "name": "box"
    }))
    .unwrap();
    assert_eq!(parsed.language, "python");
    assert_eq!(parsed.name, "box");
}

#[test]
fn raw_exec_result_default_success_true() {
    let r = RawExecResult::default();
    assert!(r.success);
    assert_eq!(r.exit_code, 0);
    // decode default: missing `success`/`stderr` fail-open to the
    // construction defaults (true / "").
    let parsed: RawExecResult = serde_json::from_value(serde_json::json!({
        "exit_code": 3,
        "stdout": "hi"
    }))
    .unwrap();
    assert_eq!(parsed.exit_code, 3);
    assert_eq!(parsed.stdout, "hi");
    assert!(parsed.success);
    assert_eq!(parsed.stderr, "");
}

#[test]
fn context_preparer_injects_docker_metadata() {
    let prep = ContextPreparer::Docker(DockerContextPreparer::new(
        "sb-1".parse().expect("non-empty id"),
    ));
    let mut ctx = JsonObject::new();
    prep.prepare_context(&mut ctx).expect("prepare");
    assert_eq!(ctx["sandbox_id"], serde_json::json!("sb-1"));
    assert_eq!(ctx["sandbox_provider"], serde_json::json!("docker"));
}
