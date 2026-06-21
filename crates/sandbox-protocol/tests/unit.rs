use sandbox_protocol::manual::render_catalog_manual;
use sandbox_protocol::{
    catalog_from_value, catalog_to_value, decode_request_object, ArgCliSpec, ArgKind, ArgSpec,
    ArgsPresence, CliSpec, OperationCatalog, OperationExecutionSpace, OperationFamily,
    OperationScope, OperationSpec,
};
use serde_json::{json, Value};

static TEST_ARGS: &[ArgSpec] = &[ArgSpec::required(
    "sandbox_id",
    ArgKind::String,
    "Sandbox id.",
    Some(ArgCliSpec {
        flag: Some("--sandbox-id"),
        positional: None,
    }),
)];

static TEST_SPEC: OperationSpec = OperationSpec {
    name: "create_sandbox",
    family: OperationFamily::Run,
    summary: "Create a sandbox.",
    args: TEST_ARGS,
    cli: Some(CliSpec {
        path: &["manager"],
        usage: "sandbox manager create_sandbox --sandbox-id ID",
        examples: &["sandbox manager create_sandbox --sandbox-id sbox-1"],
    }),
};

static TEST_SPECS: &[&OperationSpec] = &[&TEST_SPEC];

#[test]
fn decode_request_requires_object_args_when_present() {
    let value = json!({
        "op": "exec_command",
        "request_id": "req-1",
        "args": "bad",
    });
    let object = value.as_object().expect("object").clone();
    let err = decode_request_object(object, ArgsPresence::Required)
        .expect_err("non-object args rejected");
    assert_eq!(err.kind(), "invalid_request");
    assert_eq!(err.message(), "args must be an object");
}

#[test]
fn decode_request_rejects_missing_scope() {
    let value = json!({
        "op": "list_sandboxes",
        "request_id": "req-1",
        "args": {},
    });
    let object = value.as_object().expect("object").clone();
    let err = decode_request_object(object, ArgsPresence::Required)
        .expect_err("missing scope rejected");

    assert_eq!(err.kind(), "invalid_request");
    assert_eq!(err.message(), "scope is required");
}

#[test]
fn decode_request_accepts_sandbox_scope() {
    let value = json!({
        "op": "exec_command",
        "request_id": "req-1",
        "scope": {
            "kind": "sandbox",
            "sandbox_id": "sbox-1"
        },
        "args": {},
    });
    let object = value.as_object().expect("object").clone();
    let request =
        decode_request_object(object, ArgsPresence::Required).expect("request should decode");

    assert_eq!(
        request.scope,
        OperationScope::Sandbox {
            sandbox_id: "sbox-1".to_owned()
        }
    );
}

#[test]
fn decode_request_rejects_empty_sandbox_scope_id() {
    let value = json!({
        "op": "exec_command",
        "request_id": "req-1",
        "scope": {
            "kind": "sandbox",
            "sandbox_id": ""
        },
        "args": {},
    });
    let object = value.as_object().expect("object").clone();
    let err = decode_request_object(object, ArgsPresence::Required)
        .expect_err("empty sandbox id rejected");

    assert_eq!(err.kind(), "invalid_request");
    assert_eq!(err.message(), "scope sandbox_id must be non-empty");
}

#[test]
fn catalog_to_value_serializes_cli_metadata() {
    let value = catalog_to_value(OperationCatalog::new(
        OperationExecutionSpace::Manager,
        TEST_SPECS,
    ));

    assert_eq!(value["operation_execution_space"], "manager");
    assert_eq!(value["operations"][0]["name"], "create_sandbox");
    assert_eq!(value["operations"][0]["family"], "run");
    assert_eq!(value["operations"][0]["summary"], "Create a sandbox.");
    assert!(value["operations"][0]["args"].is_array());
    assert!(value["operations"][0]["cli"].is_object());
    assert_eq!(value["operations"][0]["args"][0]["name"], "sandbox_id");
    assert_eq!(value["operations"][0]["args"][0]["kind"], "string");
    assert_eq!(value["operations"][0]["args"][0]["required"], true);
    assert_eq!(value["operations"][0]["args"][0]["default"], Value::Null);
    assert_eq!(
        value["operations"][0]["args"][0]["cli"]["flag"],
        "--sandbox-id"
    );
    assert_eq!(
        value["operations"][0]["cli"]["examples"][0],
        "sandbox manager create_sandbox --sandbox-id sbox-1"
    );
}

#[test]
fn catalog_from_value_decodes_cli_metadata() {
    let value = json!({
        "operation_execution_space": "runtime",
        "operations": [
            {
                "name": "exec_command",
                "family": "command",
                "summary": "Start a command.",
                "args": [
                    {
                        "name": "cmd",
                        "kind": "string",
                        "required": true,
                        "help": "Shell command text.",
                        "default": null,
                        "cli": {
                            "flag": null,
                            "positional": "COMMAND"
                        }
                    }
                ],
                "cli": {
                    "path": ["runtime"],
                    "usage": "sandbox runtime exec_command COMMAND",
                    "examples": ["sandbox runtime exec_command pwd"]
                }
            }
        ]
    });

    let catalog = catalog_from_value(&value).expect("catalog decodes");

    assert_eq!(
        catalog.operation_execution_space,
        OperationExecutionSpace::Runtime
    );
    assert_eq!(catalog.operations[0].family, OperationFamily::Command);
    assert_eq!(
        catalog.operations[0].args[0]
            .cli
            .as_ref()
            .and_then(|cli| cli.positional.as_deref()),
        Some("COMMAND")
    );
}

#[test]
fn catalog_from_value_rejects_unknown_execution_space() {
    let value = json!({
        "operation_execution_space": "daemon",
        "operations": []
    });

    let error = catalog_from_value(&value).expect_err("unknown space rejected");

    assert_eq!(error.message(), "unknown operation_execution_space: daemon");
}

#[test]
fn catalog_from_value_rejects_missing_execution_space() {
    let value = json!({
        "operations": []
    });

    let error = catalog_from_value(&value).expect_err("missing space rejected");

    assert_eq!(
        error.message(),
        "operation_execution_space must be a string"
    );
}

#[test]
fn catalog_to_value_omits_legacy_owner_target_fields() {
    let value = catalog_to_value(OperationCatalog::new(
        OperationExecutionSpace::Manager,
        TEST_SPECS,
    ));

    assert_no_forbidden_catalog_keys(&value);
}

#[test]
fn render_catalog_manual_uses_catalog_documents() {
    let manager = catalog_from_value(&catalog_to_value(OperationCatalog::new(
        OperationExecutionSpace::Manager,
        TEST_SPECS,
    )))
    .expect("manager catalog");

    let manual = render_catalog_manual(&manager, None);

    assert!(manual.contains("Sandbox Manager Operations"));
    assert!(manual.contains("create_sandbox"));
    assert!(manual.contains("--sandbox-id: string (required)"));
    assert!(manual.contains("Sandbox Runtime Operations"));
    assert!(manual.contains("runtime catalog requires --sandbox-id"));
}

fn assert_no_forbidden_catalog_keys(value: &serde_json::Value) {
    match value {
        serde_json::Value::Object(object) => {
            for key in [
                "owner",
                "target",
                "route",
                "implementation_owner",
                "operation_target",
            ] {
                assert!(!object.contains_key(key), "catalog emitted forbidden {key}");
            }
            for child in object.values() {
                assert_no_forbidden_catalog_keys(child);
            }
        }
        serde_json::Value::Array(values) => {
            for child in values {
                assert_no_forbidden_catalog_keys(child);
            }
        }
        serde_json::Value::Null
        | serde_json::Value::Bool(_)
        | serde_json::Value::Number(_)
        | serde_json::Value::String(_) => {}
    }
}
