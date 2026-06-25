use crate::{
    CreateSandboxRequest, ManagerError, SandboxDaemonEndpoint, SandboxRecord, SandboxState,
};

use super::{image, record_value, workspace_root};
use sandbox_protocol::{ArgCliSpec, ArgKind, ArgSpec, CliOperationSpec, CliSpec};

pub(crate) const SPEC: CliOperationSpec = CliOperationSpec {
    name: "create_sandbox",
    family: "management",
    summary: "Create a host-side sandbox record and runtime sandbox.",
    description:
        "Create a host-side sandbox record, create the runtime sandbox, and start its daemon.",
    args: CREATE_SANDBOX_ARGS,
    cli: Some(CREATE_SANDBOX_CLI),
    related: &["list_sandboxes", "inspect_sandbox", "destroy_sandbox"],
};

const CREATE_SANDBOX_ARGS: &[ArgSpec] = &[
    ArgSpec::required(
        "image",
        ArgKind::String,
        "Container image used to create the sandbox.",
        Some(ArgCliSpec {
            flag: Some("--image"),
            positional: None,
        }),
    ),
    ArgSpec::required(
        "workspace_root",
        ArgKind::Path,
        "Absolute workspace root mounted inside this sandbox.",
        Some(ArgCliSpec {
            flag: Some("--workspace-root"),
            positional: None,
        }),
    ),
];

const CREATE_SANDBOX_CLI: CliSpec = CliSpec {
    path: &["manager", "create_sandbox"],
    usage: "sandbox-cli manager create_sandbox --image IMAGE --workspace-root PATH",
    examples: &[
        "sandbox-cli manager create_sandbox --image ubuntu:24.04 --workspace-root /testbed",
    ],
};

pub(crate) fn dispatch(
    services: &crate::operation::ManagerServices,
    request: &sandbox_protocol::Request,
) -> sandbox_protocol::Response {
    let image = match image(request) {
        Ok(image) => image,
        Err(response) => return response,
    };
    let workspace_root = match workspace_root(request) {
        Ok(workspace_root) => workspace_root,
        Err(response) => return response,
    };
    let create_request = CreateSandboxRequest {
        image,
        workspace_root: workspace_root.clone(),
    };
    let created = match services.runtime.create_sandbox(&create_request) {
        Ok(created) => created,
        Err(error) => return error.into_response(),
    };
    let id = created.id;
    let record = match services.store.create(id.clone(), workspace_root.clone()) {
        Ok(record) => record,
        Err(error) => {
            let untracked = SandboxRecord::new(id, workspace_root, SandboxState::Creating);
            let _ = services.runtime.destroy_sandbox(&untracked);
            return error.into_response();
        }
    };
    let endpoint = match provision_daemon(services, &record) {
        Ok(endpoint) => endpoint,
        Err(error) => {
            rollback(services, &record);
            return error.into_response();
        }
    };
    if let Err(error) = services.store.update_endpoint(&id, Some(endpoint)) {
        rollback(services, &record);
        return error.into_response();
    }
    match services
        .store
        .transition_state(&id, SandboxState::Creating, SandboxState::Ready)
    {
        Ok(ready) => sandbox_protocol::Response::ok(record_value(ready)),
        Err(error) => {
            rollback(services, &record);
            error.into_response()
        }
    }
}

fn provision_daemon(
    services: &crate::operation::ManagerServices,
    record: &SandboxRecord,
) -> Result<SandboxDaemonEndpoint, ManagerError> {
    services.daemon_installer.install_daemon(record)?;
    let endpoint = services.daemon_installer.start_daemon(record)?;
    services.daemon_installer.check_daemon(record, &endpoint)?;
    Ok(endpoint)
}

fn rollback(services: &crate::operation::ManagerServices, record: &SandboxRecord) {
    let _ = services.daemon_installer.stop_daemon(record);
    let _ = services.runtime.destroy_sandbox(record);
    let _ = services.store.remove(&record.id);
}
