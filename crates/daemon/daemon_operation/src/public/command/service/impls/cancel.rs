use std::time::Instant;

use super::command_yield_response;
use crate::command::service::CommandOperationService;
use crate::command::{
    CancelCommandInput, CancellationState, CommandCallContext, CommandId, CommandLifecycleState,
    CommandServiceError, CommandYield,
};
use crate::operation::{
    ArgCliSpec, ArgKind, ArgSpec, CliSpec, OperationFamily, OperationRequest, OperationResponse,
    OperationSpec,
};
use crate::workspace_crate::CallerId;
use crate::DaemonOperations;

pub(crate) const SPEC: OperationSpec = OperationSpec {
    name: "cancel",
    family: OperationFamily::Command,
    summary: "Request cancellation of a running command.",
    args: CANCEL_ARGS,
    cli: Some(CANCEL_CLI),
};

const CANCEL_ARGS: &[ArgSpec] = &[ArgSpec::required(
    "command_id",
    ArgKind::String,
    "Command id returned by exec_command.",
    Some(ArgCliSpec {
        flag: None,
        positional: Some("COMMAND_ID"),
    }),
)];

const CANCEL_CLI: CliSpec = CliSpec {
    path: &["daemon", "commands", "cancel"],
    usage: "ephai-sandbox-gateway daemon --sandbox-id SID commands cancel COMMAND_ID",
    examples: &["ephai-sandbox-gateway daemon --sandbox-id sb-1 commands cancel cmd-1"],
};

pub(crate) fn dispatch(
    operations: &DaemonOperations,
    request: OperationRequest<'_>,
) -> OperationResponse {
    let input = match parse_input(&request) {
        Ok(input) => input,
        Err(response) => return response,
    };
    let context = match parse_context(&request) {
        Ok(context) => context,
        Err(response) => return response,
    };
    command_yield_response(&request, operations.command.cancel(input, context))
}

fn parse_input(request: &OperationRequest<'_>) -> Result<CancelCommandInput, OperationResponse> {
    Ok(CancelCommandInput {
        command_id: CommandId(request.required_string("command_id")?),
    })
}

fn parse_context(request: &OperationRequest<'_>) -> Result<CommandCallContext, OperationResponse> {
    Ok(CommandCallContext {
        caller_id: CallerId(request.optional_string("caller_id")?.unwrap_or_default()),
    })
}

impl CommandOperationService {
    pub fn cancel(
        &self,
        input: CancelCommandInput,
        context: CommandCallContext,
    ) -> Result<CommandYield, CommandServiceError> {
        let command_id = input.command_id;
        self.ensure_active_owner(&command_id, &context.caller_id)?;
        let output = self
            .process_store()
            .update_active(&command_id, |active| {
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
                command_id: command_id.clone(),
            })?;

        Ok(Self::running_command_yield(command_id, output))
    }
}
