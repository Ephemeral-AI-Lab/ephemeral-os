use std::time::Instant;

use super::command_yield_response;
use crate::command::service::CommandOperationService;
use crate::command::{
    CancelCommandInput, CancellationState, CommandLifecycleState, CommandServiceError,
    CommandSessionId, CommandYield,
};
use crate::operation::{
    ArgCliSpec, ArgKind, ArgSpec, CliSpec, OperationFamily, OperationRequest, OperationResponse,
    OperationSpec,
};
use crate::SandboxDaemonOperations;

pub(crate) const SPEC: OperationSpec = OperationSpec {
    name: "cancel_command",
    family: OperationFamily::Command,
    summary: "Request cancellation of a running command.",
    args: CANCEL_ARGS,
    cli: Some(CANCEL_CLI),
};

const CANCEL_ARGS: &[ArgSpec] = &[ArgSpec::required(
    "command_session_id",
    ArgKind::String,
    "Command session id returned by exec_command.",
    Some(ArgCliSpec {
        flag: Some("--command-session-id"),
        positional: None,
    }),
)];

const CANCEL_CLI: CliSpec = CliSpec {
    path: &["daemon", "commands", "cancel_command"],
    usage: "cancel_command {\"command_session_id\":\"ID\"}",
    examples: &["cancel_command {\"command_session_id\":\"cmd-1\"}"],
};

pub(crate) fn dispatch(
    operations: &SandboxDaemonOperations,
    request: OperationRequest<'_>,
) -> OperationResponse {
    let input = match parse_input(&request) {
        Ok(input) => input,
        Err(response) => return response,
    };
    command_yield_response(&request, operations.command.cancel(input))
}

fn parse_input(request: &OperationRequest<'_>) -> Result<CancelCommandInput, OperationResponse> {
    Ok(CancelCommandInput {
        command_session_id: CommandSessionId(request.required_string("command_session_id")?),
    })
}

impl CommandOperationService {
    pub fn cancel(&self, input: CancelCommandInput) -> Result<CommandYield, CommandServiceError> {
        let command_session_id = input.command_session_id;
        self.ensure_active_command(&command_session_id)?;
        let output = self
            .process_store()
            .update_active(&command_session_id, |active| {
                if let Some(token) = active.remount_cancellation.clone() {
                    token.request_cancel();
                } else {
                    active.process.cancel_process();
                    active.lifecycle_state = CommandLifecycleState::Cancelled;
                }
                active.cancellation = CancellationState::Requested {
                    requested_at: Instant::now(),
                };
                active.process.read_output_since(0)
            })
            .ok_or_else(|| CommandServiceError::CommandNotFound {
                command_session_id: command_session_id.clone(),
            })?;

        Ok(Self::running_command_yield(command_session_id, output))
    }
}
