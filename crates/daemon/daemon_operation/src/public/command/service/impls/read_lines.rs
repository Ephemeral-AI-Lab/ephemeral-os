use super::command_lines_response;
use crate::command::service::transcript::CommandTranscriptWindowExt;
use crate::command::service::CommandOperationService;
use crate::command::{
    CommandCallContext, CommandId, CommandLinesOutput, CommandServiceError, CommandStatus,
    ReadCommandLinesInput,
};
use crate::operation::{
    ArgCliSpec, ArgKind, ArgSpec, CliSpec, OperationFamily, OperationRequest, OperationResponse,
    OperationSpec,
};
use crate::workspace_crate::CallerId;
use crate::DaemonOperations;

pub(crate) const SPEC: OperationSpec = OperationSpec {
    name: "read_lines",
    family: OperationFamily::Command,
    summary: "Read a retained command transcript window by line offset.",
    args: READ_LINES_ARGS,
    cli: Some(READ_LINES_CLI),
};

const READ_LINES_ARGS: &[ArgSpec] = &[
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
        "offset",
        ArgKind::Integer,
        "First transcript line offset.",
        Some(ArgCliSpec {
            flag: Some("--offset"),
            positional: None,
        }),
    ),
    ArgSpec::required(
        "limit",
        ArgKind::Integer,
        "Maximum transcript rows to return.",
        Some(ArgCliSpec {
            flag: Some("--limit"),
            positional: None,
        }),
    ),
];

const READ_LINES_CLI: CliSpec = CliSpec {
    path: &["daemon", "commands", "read-lines"],
    usage: "ephai-sandbox-gateway daemon --sandbox-id SID commands read-lines --offset N --limit N COMMAND_ID",
    examples: &[
        "ephai-sandbox-gateway daemon --sandbox-id sb-1 commands read-lines --offset 0 --limit 100 cmd-1",
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
    command_lines_response(&request, operations.command.read_lines(input, context))
}

fn parse_input(request: &OperationRequest<'_>) -> Result<ReadCommandLinesInput, OperationResponse> {
    Ok(ReadCommandLinesInput {
        command_id: CommandId(request.required_string("command_id")?),
        offset: request.required_u64("offset")?,
        limit: request.required_usize("limit")?,
    })
}

fn parse_context(request: &OperationRequest<'_>) -> Result<CommandCallContext, OperationResponse> {
    Ok(CommandCallContext {
        caller_id: CallerId(request.optional_string("caller_id")?.unwrap_or_default()),
    })
}

impl CommandOperationService {
    pub fn read_lines(
        &self,
        input: ReadCommandLinesInput,
        context: CommandCallContext,
    ) -> Result<CommandLinesOutput, CommandServiceError> {
        let command_id = input.command_id;
        if let Some(active) = self.active_for_owner_or_none(&command_id, &context.caller_id)? {
            let transcript = active.transcript.clone();
            drop(active);
            return Ok(transcript.window(input.offset, input.limit).into_output(
                command_id,
                CommandStatus::Running,
                None,
            ));
        }

        let completed = self.completed_for_owner(&command_id, &context.caller_id)?;
        Ok(completed
            .transcript
            .window(&command_id, input.offset, input.limit)?
            .into_output(
                command_id,
                completed.result.status,
                completed.result.exit_code,
            ))
    }
}
