use super::{inspect_response, validate_ids};
use crate::cgroup_monitor::{
    CgroupMonitorOperationService, CgroupMonitorServiceError, InspectCgroupMonitorInput,
    InspectCgroupMonitorOutput,
};
use crate::command::CommandSessionId;
use crate::operation::{ArgCliSpec, ArgKind, ArgSpec, CliSpec, OperationSpec};
use crate::workspace_crate::WorkspaceSessionId;
use crate::SandboxRuntimeOperations;
use sandbox_protocol::{Request, Response};

pub(crate) const SPEC: OperationSpec = OperationSpec {
    name: "inspect_cgroup_monitor",
    family: "cgroup_monitor",
    summary: "Inspect the latest cgroup monitor state.",
    description:
        "Inspect the latest retained cgroup monitor state for a workspace session or command session.",
    args: INSPECT_CGROUP_MONITOR_ARGS,
    cli: Some(INSPECT_CGROUP_MONITOR_CLI),
    related: &["read_cgroup_monitor_samples"],
};

const INSPECT_CGROUP_MONITOR_ARGS: &[ArgSpec] = &[
    ArgSpec::required(
        "workspace_session_id",
        ArgKind::String,
        "Workspace session id to inspect.",
        Some(ArgCliSpec {
            flag: Some("--workspace-session-id"),
            positional: None,
        }),
    ),
    ArgSpec::optional(
        "command_session_id",
        ArgKind::String,
        "Command session id to inspect under the workspace session.",
        None,
        Some(ArgCliSpec {
            flag: Some("--command-session-id"),
            positional: None,
        }),
    ),
];

const INSPECT_CGROUP_MONITOR_CLI: CliSpec = CliSpec {
    path: &["runtime", "inspect_cgroup_monitor"],
    usage: "sandbox-cli runtime inspect_cgroup_monitor --workspace-session-id ID [--command-session-id CMD]",
    examples: &[
        "sandbox-cli runtime inspect_cgroup_monitor --workspace-session-id ws-1",
        "sandbox-cli runtime inspect_cgroup_monitor --workspace-session-id ws-1 --command-session-id cmd-1",
    ],
};

pub(crate) fn dispatch(operations: &SandboxRuntimeOperations, request: &Request) -> Response {
    let input = match parse_input(request) {
        Ok(input) => input,
        Err(response) => return response,
    };
    inspect_response(operations.cgroup_monitor.inspect_cgroup_monitor(input))
}

fn parse_input(request: &Request) -> Result<InspectCgroupMonitorInput, Response> {
    Ok(InspectCgroupMonitorInput {
        workspace_session_id: WorkspaceSessionId(request.required_string("workspace_session_id")?),
        command_session_id: request
            .optional_string("command_session_id")?
            .map(CommandSessionId),
    })
}

impl CgroupMonitorOperationService {
    pub fn inspect_cgroup_monitor(
        &self,
        input: InspectCgroupMonitorInput,
    ) -> Result<InspectCgroupMonitorOutput, CgroupMonitorServiceError> {
        validate_ids(
            &input.workspace_session_id,
            input.command_session_id.as_ref(),
        )?;
        self.ensure_session_target_registered(&input.workspace_session_id)?;
        let snapshot = self
            .registry()
            .inspect(
                &input.workspace_session_id,
                input.command_session_id.as_ref().map(|id| id.0.as_str()),
            )
            .ok_or_else(|| target_not_found(&input))?;
        Ok(InspectCgroupMonitorOutput {
            workspace_session_id: input.workspace_session_id,
            command_session_id: input.command_session_id,
            target: snapshot.target,
            monitor: snapshot.monitor,
            latest: snapshot.latest,
            cleanup: snapshot.cleanup,
        })
    }

    pub(crate) fn ensure_session_target_registered(
        &self,
        workspace_session_id: &WorkspaceSessionId,
    ) -> Result<(), CgroupMonitorServiceError> {
        let handler = self
            .workspace()
            .resolve_session(workspace_session_id.clone())?;
        self.registry()
            .register_session_from_handle(&handler.handle);
        Ok(())
    }
}

fn target_not_found(input: &InspectCgroupMonitorInput) -> CgroupMonitorServiceError {
    match input.command_session_id.clone() {
        Some(command_session_id) => CgroupMonitorServiceError::CommandTargetNotFound {
            workspace_session_id: input.workspace_session_id.clone(),
            command_session_id,
        },
        None => CgroupMonitorServiceError::SessionTargetNotFound {
            workspace_session_id: input.workspace_session_id.clone(),
        },
    }
}
