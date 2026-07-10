use http::StatusCode;
use serde_json::Value;

use crate::support;

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

#[tokio::test]
async fn catalog_returns_all_three_execution_spaces() {
    let gateway = support::FakeGateway::spawn(|_| Vec::new()).await;
    let console = support::spawn_console_default(gateway.addr).await;

    let response = support::get(console, "/api/catalog").await;
    assert_eq!(response.status(), StatusCode::OK);
    let body = support::body_json(response).await;

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

    assert_eq!(gateway.request_count(), 0, "catalog never hits the gateway");
}
