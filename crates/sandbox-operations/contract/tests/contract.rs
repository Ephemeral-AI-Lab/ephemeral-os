use sandbox_operation_contract::{
    catalog_from_value, catalog_to_value, ArgKind, ArgSpec, OperationCatalog, OperationDomain,
    OperationExecutionOwner, OperationFamilySpec, OperationResponse, OperationRouteSpec,
    OperationScope, OperationScopeKind, OperationScopePolicy, OperationSpec, OperationVisibility,
};
use serde_json::{json, Value};

static TEST_ARGS: &[ArgSpec] = &[
    ArgSpec::required("image", ArgKind::String, "Container image."),
    ArgSpec::optional("retries", ArgKind::Integer, "Retry count.", Some("1")),
];

static TEST_FAMILY: OperationFamilySpec = OperationFamilySpec {
    id: "management",
    title: "Management",
    summary: "Manage sandboxes.",
    description: "Create and inspect sandboxes.",
};

static TEST_SPEC: OperationSpec = OperationSpec {
    name: "create_sandbox",
    family: "management",
    summary: "Create a sandbox.",
    description: "Create a sandbox and start its daemon.",
    args: TEST_ARGS,
    related: &[],
};

static TEST_FAMILIES: &[&OperationFamilySpec] = &[&TEST_FAMILY];
static TEST_SPECS: &[&OperationSpec] = &[&TEST_SPEC];
static TEST_ROUTES: &[OperationRouteSpec] = &[OperationRouteSpec {
    operation: "create_sandbox",
    scope_policy: OperationScopePolicy::System,
    scope_kind: OperationScopeKind::System,
    execution_owner: OperationExecutionOwner::Manager,
    visibility: OperationVisibility::Public,
}];

#[test]
fn responses_preserve_payload_owned_shape() {
    let ok = OperationResponse::ok(json!({
        "status": "ok",
        "output": "command output remains payload-owned",
    }))
    .into_json_value();
    let err = OperationResponse::fault("operation_failed", "failed").into_json_value();

    assert_eq!(ok["status"], "ok");
    assert!(ok.get("result").is_none(), "{ok}");
    assert!(ok.get("meta").is_none(), "{ok}");
    assert_eq!(err["error"]["kind"], "operation_failed");
    assert!(err.get("result").is_none(), "{err}");
    assert!(err.get("meta").is_none(), "{err}");
}

#[test]
fn scope_preserves_sandbox_id_and_kind() {
    let scope = OperationScope::sandbox("sbox-1");

    assert_eq!(scope.sandbox_id(), Some("sbox-1"));
    assert_eq!(scope.kind(), OperationScopeKind::Sandbox);
    assert!(scope.is_sandbox());
}

#[test]
fn catalog_serializes_only_semantic_fields() {
    let value = catalog_to_value(OperationCatalog::new(
        OperationDomain::Manager,
        TEST_FAMILIES,
        TEST_SPECS,
        TEST_ROUTES,
    ));

    assert_eq!(value["operation_execution_space"], "manager");
    assert_eq!(value["operations"][0]["name"], "create_sandbox");
    assert_eq!(value["operations"][0]["args"][0]["kind"], "string");
    assert_eq!(value["routes"][0]["scope_policy"], "system");
    assert_eq!(value["routes"][0]["execution_owner"], "manager");
    assert!(value["operations"][0].get("cli").is_none());
    assert!(value["operations"][0]["args"][0].get("cli").is_none());
}

#[test]
fn catalog_decodes_semantic_document() {
    let catalog = catalog_from_value(&catalog_value()).expect("catalog decodes");

    assert_eq!(catalog.operation_execution_space, OperationDomain::Runtime);
    assert_eq!(catalog.operations[0].args[0].kind, ArgKind::JsonArray);
    assert_eq!(catalog.routes[0].operation, "file_edit");
    assert_eq!(
        catalog.routes[0].scope_policy,
        OperationScopePolicy::SandboxRequired
    );
}

#[test]
fn catalog_rejects_unknown_domain() {
    let mut value = catalog_value();
    value["operation_execution_space"] = json!("daemon");

    let error = catalog_from_value(&value).expect_err("unknown domain rejected");

    assert_eq!(error.message(), "unknown operation_execution_space: daemon");
}

#[test]
fn catalog_rejects_missing_domain() {
    let mut value = catalog_value();
    value
        .as_object_mut()
        .expect("catalog object")
        .remove("operation_execution_space");

    let error = catalog_from_value(&value).expect_err("missing domain rejected");

    assert_eq!(
        error.message(),
        "operation_execution_space must be a string"
    );
}

#[test]
fn catalog_rejects_duplicate_families() {
    let mut value = catalog_value();
    value["families"] = json!([
        family_value("file", "File"),
        family_value("file", "File Again")
    ]);

    let error = catalog_from_value(&value).expect_err("duplicate family rejected");

    assert_eq!(error.message(), "duplicate operation family id: file");
}

#[test]
fn catalog_rejects_unknown_family() {
    let mut value = catalog_value();
    value["operations"][0]["family"] = json!("missing");

    let error = catalog_from_value(&value).expect_err("unknown family rejected");

    assert_eq!(
        error.message(),
        "operation file_edit references unknown family: missing"
    );
}

#[test]
fn catalog_rejects_duplicate_operations() {
    let mut value = catalog_value();
    let operation = value["operations"][0].clone();
    value["operations"] = json!([operation.clone(), operation]);

    let error = catalog_from_value(&value).expect_err("duplicate operation rejected");

    assert_eq!(error.message(), "duplicate operation name: file_edit");
}

#[test]
fn catalog_rejects_unknown_related_operation() {
    let mut value = catalog_value();
    value["operations"][0]["related"] = json!(["missing"]);

    let error = catalog_from_value(&value).expect_err("unknown relation rejected");

    assert_eq!(
        error.message(),
        "operation file_edit references unknown related operation: missing"
    );
}

#[test]
fn catalog_rejects_incomplete_system_or_sandbox_routes() {
    let mut value = catalog_value();
    value["routes"][0]["scope_policy"] = json!("system_or_sandbox");

    let error = catalog_from_value(&value).expect_err("incomplete route expansion rejected");

    assert_eq!(
        error.message(),
        "operation route expansion does not match scope policy: file_edit"
    );
}

#[test]
fn catalog_rejects_mixed_scope_policies() {
    let mut value = catalog_value();
    value["routes"][0]["scope_policy"] = json!("system_or_sandbox");
    let mut system_route = value["routes"][0].clone();
    system_route["scope_policy"] = json!("system");
    system_route["scope_kind"] = json!("system");
    value["routes"]
        .as_array_mut()
        .expect("routes array")
        .push(system_route);

    let error = catalog_from_value(&value).expect_err("mixed route policies rejected");

    assert_eq!(
        error.message(),
        "operation routes use mixed scope policies: file_edit"
    );
}

fn catalog_value() -> Value {
    json!({
        "operation_execution_space": "runtime",
        "families": [family_value("file", "File")],
        "operations": [{
            "name": "file_edit",
            "family": "file",
            "summary": "Edit a file.",
            "description": "Edit a file atomically.",
            "args": [{
                "name": "edits",
                "kind": "json_array",
                "required": true,
                "help": "Edit operations.",
                "default": null
            }],
            "related": []
        }],
        "routes": [{
            "operation": "file_edit",
            "scope_policy": "sandbox_required",
            "scope_kind": "sandbox",
            "execution_owner": "runtime",
            "visibility": "public"
        }]
    })
}

fn family_value(id: &str, title: &str) -> Value {
    json!({
        "id": id,
        "title": title,
        "summary": format!("{title} summary"),
        "description": format!("{title} description"),
    })
}
