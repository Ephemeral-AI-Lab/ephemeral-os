use super::command_lines_response;
use crate::command::service::transcript::CommandTranscriptWindowExt;
use crate::command::service::CommandOperationService;
use crate::command::{
    CommandLinesOutput, CommandServiceError, CommandSessionId, CommandStatus, ReadCommandLinesInput,
};
use crate::operation::{
    ArgCliSpec, ArgKind, ArgSpec, CliSpec, OperationFamily, OperationRequest, OperationResponse,
    OperationSpec,
};
use crate::DaemonOperations;

pub(crate) const SPEC: OperationSpec = OperationSpec {
    name: "read_command_lines",
    family: OperationFamily::Command,
    summary: "Read a retained command transcript window by line offset.",
    args: READ_LINES_ARGS,
    cli: Some(READ_LINES_CLI),
};

const READ_LINES_ARGS: &[ArgSpec] = &[
    ArgSpec::required(
        "command_session_id",
        ArgKind::String,
        "Command session id returned by exec_command.",
        Some(ArgCliSpec {
            flag: None,
            positional: Some("COMMAND_SESSION_ID"),
        }),
    ),
    ArgSpec::required(
        "start_offset",
        ArgKind::Integer,
        "First transcript line offset.",
        Some(ArgCliSpec {
            flag: Some("--start-offset"),
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
    path: &["daemon", "commands", "read-command-lines"],
    usage: "ephai-sandbox-gateway daemon --sandbox-id SID commands read-command-lines --start-offset N --limit N COMMAND_SESSION_ID",
    examples: &[
        "ephai-sandbox-gateway daemon --sandbox-id sb-1 commands read-command-lines --start-offset 0 --limit 100 cmd-1",
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
    command_lines_response(&request, operations.command.read_command_lines(input))
}

fn parse_input(request: &OperationRequest<'_>) -> Result<ReadCommandLinesInput, OperationResponse> {
    Ok(ReadCommandLinesInput {
        command_session_id: CommandSessionId(request.required_string("command_session_id")?),
        start_offset: request.required_u64("start_offset")?,
        limit: request.required_usize("limit")?,
    })
}

impl CommandOperationService {
    pub fn read_command_lines(
        &self,
        input: ReadCommandLinesInput,
    ) -> Result<CommandLinesOutput, CommandServiceError> {
        let command_session_id = input.command_session_id;
        if let Some(active) = self.active_command_or_none(&command_session_id)? {
            let transcript = active.transcript.clone();
            drop(active);
            return Ok(transcript
                .window(input.start_offset, input.limit)
                .into_output(command_session_id, CommandStatus::Running, None));
        }

        let completed = self.completed_command(&command_session_id)?;
        Ok(completed
            .transcript
            .window(&command_session_id, input.start_offset, input.limit)?
            .into_output(
                command_session_id,
                completed.result.status,
                completed.result.exit_code,
            ))
    }
}
