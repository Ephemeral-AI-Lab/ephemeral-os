use std::sync::Arc;

use sandbox_runtime_command::yield_wait_loop::WaitOutcome;

use super::command_yield_response;
use crate::command::service::CommandOperationService;
use crate::command::{CommandServiceError, CommandSessionId, CommandYield, WriteCommandStdinInput};
use crate::operation::{ArgCliSpec, ArgKind, ArgSpec, CliSpec, OperationSpec};
use crate::SandboxRuntimeOperations;
use sandbox_protocol::{Request, Response};

pub(crate) const SPEC: OperationSpec = OperationSpec {
    name: "write_command_stdin",
    summary: "Write text to a running command stdin.",
    args: WRITE_STDIN_ARGS,
    cli: Some(WRITE_STDIN_CLI),
};

const WRITE_STDIN_ARGS: &[ArgSpec] = &[
    ArgSpec::required(
        "command_session_id",
        ArgKind::String,
        "Command session id returned by exec_command.",
        Some(ArgCliSpec {
            flag: Some("--command-session-id"),
            positional: None,
        }),
    ),
    ArgSpec::required(
        "stdin",
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
    path: &["runtime", "write_command_stdin"],
    usage: "sandbox-cli runtime --sandbox-id ID write_command_stdin --command-session-id ID TEXT",
    examples: &[
        "sandbox-cli runtime --sandbox-id sbox-1 write_command_stdin --command-session-id cmd-1 hello",
    ],
};

pub(crate) fn dispatch(operations: &SandboxRuntimeOperations, request: &Request) -> Response {
    let input = match parse_input(request) {
        Ok(input) => input,
        Err(response) => return response,
    };
    command_yield_response(operations.command.write_command_stdin(input))
}

fn parse_input(request: &Request) -> Result<WriteCommandStdinInput, Response> {
    Ok(WriteCommandStdinInput {
        command_session_id: CommandSessionId(request.required_string("command_session_id")?),
        stdin: request.required_string("stdin")?,
        yield_time_ms: request.optional_u64("yield_time_ms")?,
    })
}

impl CommandOperationService {
    pub fn write_command_stdin(
        &self,
        input: WriteCommandStdinInput,
    ) -> Result<CommandYield, CommandServiceError> {
        let command_session_id = input.command_session_id;
        let yield_time_ms = input.yield_time_ms.unwrap_or(1000);
        let (process, workspace_session_id) = {
            let active = self.active_command(&command_session_id)?;
            (
                Arc::clone(&active.process),
                active.workspace_session_id.clone(),
            )
        };
        self.ensure_workspace_session_not_remount_pending(&workspace_session_id)?;
        let start_offset = process.transcript_len();
        process.write_process_stdin(&input.stdin).map_err(|error| {
            CommandServiceError::CommandIo {
                command_session_id: command_session_id.clone(),
                error: error.to_string(),
            }
        })?;

        let outcome = if yield_time_ms == 0 {
            WaitOutcome::Running(String::new())
        } else {
            self.launch_driver().wait_for_initial_yield(
                process.as_ref(),
                yield_time_ms,
                start_offset,
            )
        };

        self.command_yield_from_wait_outcome(command_session_id, outcome)
    }
}
