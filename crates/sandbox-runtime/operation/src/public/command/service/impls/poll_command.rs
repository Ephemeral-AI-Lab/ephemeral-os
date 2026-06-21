use super::command_poll_response;
use crate::command::service::CommandOperationService;
use crate::command::{
    CommandOutputSnapshot, CommandPollOutput, CommandServiceError, CommandSessionId, CommandStatus,
    PollCommandInput,
};
use crate::operation::{ArgCliSpec, ArgKind, ArgSpec, CliSpec, OperationSpec};
use crate::SandboxRuntimeOperations;
use sandbox_protocol::{Request, Response};

pub(crate) const SPEC: OperationSpec = OperationSpec {
    name: "poll_command",
    summary: "Poll a command status and recent output.",
    args: POLL_ARGS,
    cli: Some(POLL_CLI),
};

const POLL_ARGS: &[ArgSpec] = &[
    ArgSpec::required(
        "command_session_id",
        ArgKind::String,
        "Command session id returned by exec_command.",
        Some(ArgCliSpec {
            flag: Some("--command-session-id"),
            positional: None,
        }),
    ),
    ArgSpec::optional(
        "last_n_lines",
        ArgKind::Integer,
        "Limit output to the most recent line count.",
        None,
        Some(ArgCliSpec {
            flag: Some("--last-n-lines"),
            positional: None,
        }),
    ),
];

const POLL_CLI: CliSpec = CliSpec {
    path: &["runtime", "poll_command"],
    usage: "sandbox-cli runtime --sandbox-id ID poll_command --command-session-id ID --last-n-lines N",
    examples: &[
        "sandbox-cli runtime --sandbox-id sbox-1 poll_command --command-session-id cmd-1 --last-n-lines 50",
    ],
};

pub(crate) fn dispatch(operations: &SandboxRuntimeOperations, request: &Request) -> Response {
    let input = match parse_input(request) {
        Ok(input) => input,
        Err(response) => return response,
    };
    command_poll_response(operations.command.poll_command(input))
}

fn parse_input(request: &Request) -> Result<PollCommandInput, Response> {
    Ok(PollCommandInput {
        command_session_id: CommandSessionId(request.required_string("command_session_id")?),
        last_n_lines: request.optional_usize("last_n_lines")?,
    })
}

impl CommandOperationService {
    pub fn poll_command(
        &self,
        input: PollCommandInput,
    ) -> Result<CommandPollOutput, CommandServiceError> {
        let command_session_id = input.command_session_id;
        if let Some(active) = self.active_command_or_none(&command_session_id)? {
            if active.process.process_group_id().is_some() {
                if let Some(process_exit) = active.process.take_exit() {
                    drop(active);
                    let result = self.finalize_command(command_session_id.clone(), process_exit)?;
                    let completed = self.completed_command(&command_session_id)?;
                    let stdout = input.last_n_lines.map_or_else(
                        || result.stdout.clone(),
                        |last_n_lines| {
                            ::sandbox_runtime_command::tail_lines(&result.stdout, last_n_lines)
                        },
                    );
                    return Ok(CommandPollOutput {
                        command_session_id,
                        status: result.status,
                        exit_code: result.exit_code,
                        output: CommandOutputSnapshot { stdout },
                        finalized: completed.finalized,
                    });
                }
            }
            let stdout = active
                .process
                .read_recent_output(input.last_n_lines.unwrap_or(200));
            return Ok(CommandPollOutput {
                command_session_id,
                status: CommandStatus::Running,
                exit_code: None,
                output: CommandOutputSnapshot { stdout },
                finalized: None,
            });
        }

        let completed = self.completed_command(&command_session_id)?;
        let stdout = input.last_n_lines.map_or_else(
            || completed.result.stdout.clone(),
            |last_n_lines| {
                ::sandbox_runtime_command::tail_lines(&completed.result.stdout, last_n_lines)
            },
        );
        Ok(CommandPollOutput {
            command_session_id,
            status: completed.result.status,
            exit_code: completed.result.exit_code,
            output: CommandOutputSnapshot { stdout },
            finalized: completed.finalized,
        })
    }
}
