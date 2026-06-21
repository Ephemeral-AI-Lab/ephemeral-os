use super::records_value;
use sandbox_protocol::{CliSpec, OperationSpec};

pub(crate) const SPEC: OperationSpec = OperationSpec {
    name: "list_sandboxes",
    family: "management",
    summary: "List sandbox records known to the manager.",
    description: "List sandbox records known to the manager, including lifecycle state and configured daemon endpoint metadata.",
    args: &[],
    cli: Some(LIST_SANDBOXES_CLI),
    related: &["inspect_sandbox", "create_sandbox"],
};

const LIST_SANDBOXES_CLI: CliSpec = CliSpec {
    path: &["manager", "list_sandboxes"],
    usage: "sandbox-cli manager list_sandboxes",
    examples: &["sandbox-cli manager list_sandboxes"],
};

pub(crate) fn dispatch(
    services: &crate::operation::ManagerServices,
    _request: &sandbox_protocol::Request,
) -> sandbox_protocol::Response {
    match services.store.list() {
        Ok(records) => sandbox_protocol::Response::ok(records_value(records)),
        Err(error) => error.into_response(),
    }
}
