use std::sync::Arc;

use super::command_yield_response;
use crate::command::service::CommandOperationService;
use crate::command::{
    CommandCallContext, CommandId, CommandServiceError, CommandYield, WriteStdinInput,
};
use crate::operation::{
    ArgCliSpec, ArgKind, ArgSpec, CliSpec, OperationFamily, OperationRequest, OperationResponse,
    OperationSpec,
};
use crate::workspace_crate::CallerId;
use crate::DaemonOperations;

pub(crate) const SPEC: OperationSpec = OperationSpec {
    name: "write_stdin",
    family: OperationFamily::Command,
    summary: "Write text to a running command stdin.",
    args: WRITE_STDIN_ARGS,
    cli: Some(WRITE_STDIN_CLI),
};

const WRITE_STDIN_ARGS: &[ArgSpec] = &[
    ArgSpec::required(
        "command_id",
        ArgKind::String,
        "Command id returned by exec_command.",
        Some(ArgCliSpec {
            flag: None,
            positional: Some("COMMAND_ID"),
        }),
    ),
    ArgSpec::required(
        "chars",
        ArgKind::String,
        "Text to write to stdin.",
        Some(ArgCliSpec {
            flag: None,
            positional: Some("TEXT"),
        }),
    ),
    ArgSpec::optional(
        "yield_time_ms",
        ArgKind::Integer,
        "Output wait after writing stdin.",
        None,
        Some(ArgCliSpec {
            flag: Some("--yield-time-ms"),
            positional: None,
        }),
    ),
];

const WRITE_STDIN_CLI: CliSpec = CliSpec {
    path: &["daemon", "commands", "stdin"],
    usage: "ephai-sandbox-gateway daemon --sandbox-id SID commands stdin [--yield-time-ms MS] COMMAND_ID TEXT",
    examples: &[
        "ephai-sandbox-gateway daemon --sandbox-id sb-1 commands stdin cmd-1 'hello'",
    ],
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
    command_yield_response(&request, operations.command.write_stdin(input, context))
}

fn parse_input(request: &OperationRequest<'_>) -> Result<WriteStdinInput, OperationResponse> {
    Ok(WriteStdinInput {
        command_id: CommandId(request.required_string("command_id")?),
        chars: request.required_string("chars")?,
        yield_time_ms: request.optional_u64("yield_time_ms")?,
    })
}

fn parse_context(request: &OperationRequest<'_>) -> Result<CommandCallContext, OperationResponse> {
    Ok(CommandCallContext {
        caller_id: CallerId(request.optional_string("caller_id")?.unwrap_or_default()),
    })
}

impl CommandOperationService {
    pub fn write_stdin(
        &self,
        input: WriteStdinInput,
        context: CommandCallContext,
    ) -> Result<CommandYield, CommandServiceError> {
        let command_id = input.command_id;
        let yield_time_ms = input
            .yield_time_ms
            .unwrap_or(self.config().default_yield_time_ms);
        let (process, workspace_session_id) = {
            let active = self.active_for_owner(&command_id, &context.caller_id)?;
            (
                Arc::clone(&active.process),
                active.workspace_session_id.clone(),
            )
        };
        self.ensure_workspace_session_not_remount_pending(&workspace_session_id)?;
        let output = {
            process.write_process_stdin(&input.chars).map_err(|error| {
                CommandServiceError::CommandIo {
                    command_id: command_id.clone(),
                    error: error.to_string(),
                }
            })?;
            if yield_time_ms == 0 {
                String::new()
            } else {
                process.read_output_since(0)
            }
        };

        Ok(Self::running_command_yield(command_id, output))
    }
}
