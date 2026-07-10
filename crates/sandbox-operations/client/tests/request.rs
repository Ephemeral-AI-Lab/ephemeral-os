use sandbox_operation_client::{
    build_request_from_values, build_request_from_values_with_id, BuildRequestValueInput,
    RequestBuildError,
};
use sandbox_operation_contract::{
    ArgKind, ArgSpecDocument, OperationScope, OperationScopePolicy, OperationSpecDocument,
};
use serde_json::json;

fn spec(name: &str, args: Vec<ArgSpecDocument>) -> OperationSpecDocument {
    OperationSpecDocument {
        name: name.to_owned(),
        family: "test".to_owned(),
        summary: String::new(),
        description: String::new(),
        args,
        related: Vec::new(),
    }
}

fn arg(name: &str, kind: ArgKind, required: bool, default: Option<&str>) -> ArgSpecDocument {
    ArgSpecDocument {
        name: name.to_owned(),
        kind,
        required,
        help: String::new(),
        default: default.map(str::to_owned),
    }
}

#[test]
fn system_scope_preserves_business_sandbox_id() {
    let spec = spec(
        "destroy_sandbox",
        vec![arg("sandbox_id", ArgKind::String, true, None)],
    );
    let request = build_request_from_values_with_id(
        BuildRequestValueInput {
            spec: &spec,
            scope_policy: OperationScopePolicy::System,
            scope_selector: None,
            arguments: json!({"sandbox_id": "eos-manager"}),
        },
        "request-system",
    )
    .expect("system request");

    assert_eq!(request.scope, OperationScope::system());
    assert_eq!(request.args, json!({"sandbox_id": "eos-manager"}));
}

#[test]
fn system_scope_rejects_an_out_of_band_selector() {
    let spec = spec("list_sandboxes", Vec::new());
    let error = build_request_from_values_with_id(
        BuildRequestValueInput {
            spec: &spec,
            scope_policy: OperationScopePolicy::System,
            scope_selector: Some("eos-injected".to_owned()),
            arguments: json!({}),
        },
        "request-error",
    )
    .expect_err("system selector is rejected");

    assert_eq!(
        error.message(),
        "system-scoped operation list_sandboxes does not accept a scope selector"
    );
    assert_eq!(error.kind(), "invalid_request");
    assert_eq!(
        error.to_error_envelope(),
        json!({
            "error": {
                "kind": "invalid_request",
                "message": error.message(),
                "details": {}
            }
        })
    );
}

#[test]
fn sandbox_scope_removes_compatibility_copy_and_applies_defaults() {
    let spec = spec(
        "read_command_lines",
        vec![
            arg("command_session_id", ArgKind::String, true, None),
            arg("start_offset", ArgKind::Integer, false, Some("0")),
            arg("limit", ArgKind::Integer, false, Some("200")),
        ],
    );
    let request = build_request_from_values_with_id(
        BuildRequestValueInput {
            spec: &spec,
            scope_policy: OperationScopePolicy::SandboxRequired,
            scope_selector: Some("eos-runtime".to_owned()),
            arguments: json!({
                "sandbox_id": "eos-runtime",
                "command_session_id": "cmd-1"
            }),
        },
        "request-runtime",
    )
    .expect("sandbox request");

    assert_eq!(request.scope, OperationScope::sandbox("eos-runtime"));
    assert_eq!(
        request.args,
        json!({"command_session_id": "cmd-1", "start_offset": 0, "limit": 200})
    );
}

#[test]
fn selector_fulfills_a_semantic_sandbox_id_argument() {
    let spec = spec(
        "trace",
        vec![
            arg("sandbox_id", ArgKind::String, true, None),
            arg("trace_id", ArgKind::String, false, Some("last")),
        ],
    );
    let request = build_request_from_values_with_id(
        BuildRequestValueInput {
            spec: &spec,
            scope_policy: OperationScopePolicy::SandboxRequired,
            scope_selector: Some("eos-observe".to_owned()),
            arguments: json!({"sandbox_id": "eos-observe"}),
        },
        "request-observe",
    )
    .expect("observability request");

    assert_eq!(request.op, "trace");
    assert_eq!(request.scope, OperationScope::sandbox("eos-observe"));
    assert_eq!(request.args, json!({"trace_id": "last"}));
}

#[test]
fn system_or_sandbox_policy_uses_only_the_explicit_selector() {
    let spec = spec(
        "snapshot",
        vec![arg("sandbox_id", ArgKind::String, false, None)],
    );
    let system = build_request_from_values_with_id(
        BuildRequestValueInput {
            spec: &spec,
            scope_policy: OperationScopePolicy::SystemOrSandbox,
            scope_selector: None,
            arguments: json!({}),
        },
        "request-system",
    )
    .expect("system snapshot");
    let sandbox = build_request_from_values_with_id(
        BuildRequestValueInput {
            spec: &spec,
            scope_policy: OperationScopePolicy::SystemOrSandbox,
            scope_selector: Some("eos-observe".to_owned()),
            arguments: json!({"sandbox_id": "eos-observe"}),
        },
        "request-sandbox",
    )
    .expect("sandbox snapshot");

    assert_eq!(system.scope, OperationScope::system());
    assert_eq!(system.args, json!({}));
    assert_eq!(sandbox.scope, OperationScope::sandbox("eos-observe"));
    assert_eq!(sandbox.args, json!({}));
}

#[test]
fn compatibility_selector_never_replaces_the_explicit_selector() {
    let spec = spec(
        "trace",
        vec![arg("sandbox_id", ArgKind::String, true, None)],
    );
    let missing = build_request_from_values_with_id(
        BuildRequestValueInput {
            spec: &spec,
            scope_policy: OperationScopePolicy::SandboxRequired,
            scope_selector: None,
            arguments: json!({}),
        },
        "request-missing",
    )
    .expect_err("explicit selector is required");
    let copy_only = build_request_from_values_with_id(
        BuildRequestValueInput {
            spec: &spec,
            scope_policy: OperationScopePolicy::SandboxRequired,
            scope_selector: None,
            arguments: json!({"sandbox_id": "eos-copy"}),
        },
        "request-copy",
    )
    .expect_err("compatibility copy cannot select scope");

    assert_eq!(missing.message(), "scope selector is required for trace");
    assert_eq!(copy_only.message(), "scope selector is required for trace");
}

#[test]
fn callers_can_create_projection_errors_without_exposing_fields() {
    let error = RequestBuildError::invalid("--path is required for file_read");

    assert_eq!(error.kind(), "invalid_request");
    assert_eq!(error.message(), "--path is required for file_read");
}

#[test]
fn invalid_values_and_selectors_are_deterministic() {
    let spec = spec(
        "file_edit",
        vec![
            arg("path", ArgKind::Path, true, None),
            arg("edits", ArgKind::JsonArray, true, None),
        ],
    );
    let cases = [
        (
            Some("eos-runtime"),
            json!({"sandbox_id": "other", "path": "a", "edits": []}),
            "sandbox_id must match the scope selector",
        ),
        (
            Some("eos-runtime"),
            json!({"path": "a", "edits": "[]"}),
            "edits must be a JSON array",
        ),
        (
            Some("eos-runtime"),
            json!({"path": "a", "edits": [], "request_id": "injected"}),
            "unknown argument for file_edit: request_id",
        ),
    ];

    for (selector, arguments, expected) in cases {
        let error = build_request_from_values_with_id(
            BuildRequestValueInput {
                spec: &spec,
                scope_policy: OperationScopePolicy::SandboxRequired,
                scope_selector: selector.map(str::to_owned),
                arguments,
            },
            "request-error",
        )
        .expect_err("invalid request");
        assert_eq!(error.message(), expected);
    }
}

#[test]
fn generated_request_id_is_a_uuid() {
    let spec = spec("list_sandboxes", Vec::new());
    let request = build_request_from_values(BuildRequestValueInput {
        spec: &spec,
        scope_policy: OperationScopePolicy::System,
        scope_selector: None,
        arguments: json!({}),
    })
    .expect("request");

    uuid::Uuid::parse_str(&request.request_id).expect("UUID request id");
}
