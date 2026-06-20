use super::command_poll_response;
use crate::command::service::CommandOperationService;
use crate::command::{
    CommandCallContext, CommandId, CommandOutputSnapshot, CommandPollOutput, CommandServiceError,
    CommandStatus, PollCommandInput,
};
use crate::operation::{
    ArgCliSpec, ArgKind, ArgSpec, CliSpec, OperationFamily, OperationRequest, OperationResponse,
    OperationSpec,
};
use crate::workspace_crate::CallerId;
use crate::DaemonOperations;

pub(crate) const SPEC: OperationSpec = OperationSpec {
    name: "poll",
    family: OperationFamily::Command,
    summary: "Poll a command status and recent output.",
    args: POLL_ARGS,
    cli: Some(POLL_CLI),
};

const POLL_ARGS: &[ArgSpec] = &[
    ArgSpec::required(
        "command_id",
        ArgKind::String,
        "Command id returned by exec_command.",
        Some(ArgCliSpec {
            flag: None,
            positional: Some("COMMAND_ID"),
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
    path: &["daemon", "commands", "poll"],
    usage:
        "ephai-sandbox-gateway daemon --sandbox-id SID commands poll [--last-n-lines N] COMMAND_ID",
    examples: &[
        "ephai-sandbox-gateway daemon --sandbox-id sb-1 commands poll --last-n-lines 50 cmd-1",
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
    command_poll_response(&request, operations.command.poll(input, context))
}

fn parse_input(request: &OperationRequest<'_>) -> Result<PollCommandInput, OperationResponse> {
    Ok(PollCommandInput {
        command_id: CommandId(request.required_string("command_id")?),
        last_n_lines: request.optional_usize("last_n_lines")?,
    })
}

fn parse_context(request: &OperationRequest<'_>) -> Result<CommandCallContext, OperationResponse> {
    Ok(CommandCallContext {
        caller_id: CallerId(request.optional_string("caller_id")?.unwrap_or_default()),
    })
}

impl CommandOperationService {
    pub fn poll(
        &self,
        input: PollCommandInput,
        context: CommandCallContext,
    ) -> Result<CommandPollOutput, CommandServiceError> {
        let command_id = input.command_id;
        if let Some(active) = self.active_for_owner_or_none(&command_id, &context.caller_id)? {
            if active.process.process_group_id().is_some() {
                if let Some(process_exit) = active.process.take_exit() {
                    drop(active);
                    let result = self.finalize_command(command_id.clone(), process_exit)?;
                    let completed = self.completed_for_owner(&command_id, &context.caller_id)?;
                    let stdout = input.last_n_lines.map_or_else(
                        || result.stdout.clone(),
                        |last_n_lines| ::command::tail_lines(&result.stdout, last_n_lines),
                    );
                    return Ok(CommandPollOutput {
                        command_id,
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
                command_id,
                status: CommandStatus::Running,
                exit_code: None,
                output: CommandOutputSnapshot { stdout },
                finalized: None,
            });
        }

        let completed = self.completed_for_owner(&command_id, &context.caller_id)?;
        let stdout = input.last_n_lines.map_or_else(
            || completed.result.stdout.clone(),
            |last_n_lines| ::command::tail_lines(&completed.result.stdout, last_n_lines),
        );
        Ok(CommandPollOutput {
            command_id,
            status: completed.result.status,
            exit_code: completed.result.exit_code,
            output: CommandOutputSnapshot { stdout },
            finalized: completed.finalized,
        })
    }
}
