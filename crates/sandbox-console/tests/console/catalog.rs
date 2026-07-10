use http::StatusCode;
use serde_json::Value;

use crate::support;

const PHASE0_CATALOG: &str = include_str!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../docs/obsidian/ephemeral-os/implementation_plan/operation-migration/evidence/phase-0/console-catalog.json"
));

fn operation_names(catalog: &Value) -> Vec<String> {
    let mut names = catalog["operations"]
        .as_array()
        .expect("operations array")
        .iter()
        .map(|spec| spec["name"].as_str().expect("operation name").to_owned())
        .collect::<Vec<_>>();
    names.sort();
    names
}

fn names(values: &[&str]) -> Vec<String> {
    let mut names = values
        .iter()
        .map(|value| (*value).to_owned())
        .collect::<Vec<_>>();
    names.sort();
    names
}

fn remove_cli_fields(value: &mut Value) {
    match value {
        Value::Array(values) => values.iter_mut().for_each(remove_cli_fields),
        Value::Object(object) => {
            object.remove("cli");
            object.values_mut().for_each(remove_cli_fields);
        }
        _ => {}
    }
}

fn remove_routes(value: &mut Value) {
    for catalog in ["management", "runtime", "observability"] {
        value[catalog]
            .as_object_mut()
            .expect("catalog object")
            .remove("routes");
    }
}

#[tokio::test]
async fn catalog_returns_all_three_execution_spaces() {
    let gateway = support::FakeGateway::spawn(|_| Vec::new()).await;
    let console = support::spawn_console_default(gateway.addr).await;

    let response = support::get(console, "/api/catalog").await;
    assert_eq!(response.status(), StatusCode::OK);
    let body = support::body_json(response).await;

    let mut phase0: Value = serde_json::from_str(PHASE0_CATALOG).expect("Phase 0 catalog fixture");
    remove_cli_fields(&mut phase0);
    let mut current_semantics = body.clone();
    remove_routes(&mut current_semantics);
    assert_eq!(current_semantics, phase0);

    let mut keys = body
        .as_object()
        .expect("catalog response object")
        .keys()
        .map(String::as_str)
        .collect::<Vec<_>>();
    keys.sort();
    assert_eq!(keys, ["management", "observability", "runtime"]);

    assert_eq!(
        operation_names(&body["management"]),
        names(&[
            "create_sandbox",
            "destroy_sandbox",
            "list_sandboxes",
            "inspect_sandbox",
            "squash_layerstacks",
            "export_changes",
        ])
    );
    assert_eq!(
        operation_names(&body["runtime"]),
        names(&[
            "exec_command",
            "write_command_stdin",
            "read_command_lines",
            "file_read",
            "file_write",
            "file_edit",
            "file_blame",
        ])
    );
    assert_eq!(
        operation_names(&body["observability"]),
        names(&["snapshot", "trace", "events", "cgroup", "layerstack"])
    );
    let file_edit = body["runtime"]["operations"]
        .as_array()
        .expect("runtime operations")
        .iter()
        .find(|operation| operation["name"] == "file_edit")
        .expect("file_edit operation");
    let edits = file_edit["args"]
        .as_array()
        .expect("file_edit arguments")
        .iter()
        .find(|arg| arg["name"] == "edits")
        .expect("file_edit edits argument");
    assert_eq!(edits["kind"], "json_array");

    for catalog in ["management", "runtime", "observability"] {
        assert!(body[catalog]["routes"].is_array());
        assert!(body[catalog]["operations"]
            .as_array()
            .expect("catalog operations")
            .iter()
            .flat_map(|operation| { operation["args"].as_array().expect("operation arguments") })
            .all(|arg| arg.get("cli").is_none()));
        assert!(body[catalog]["routes"]
            .as_array()
            .expect("catalog routes")
            .iter()
            .all(|route| route["visibility"] == "public"));
    }

    assert_eq!(gateway.request_count(), 0, "catalog never hits the gateway");
}
