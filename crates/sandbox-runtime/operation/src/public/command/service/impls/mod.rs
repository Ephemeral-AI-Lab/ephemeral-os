mod cancel_command;
mod exec_command;
mod poll_command;
mod read_command_lines;
mod write_command_stdin;

use serde_json::{json, Value};

use crate::command::{
    CommandFinalizedMetadata, CommandLinesOutput, CommandPollOutput, CommandPublishStatus,
    CommandServiceError, CommandStatus, CommandStream, CommandTranscriptRow, CommandYield,
};
use crate::operation::{OperationEntry, OperationSpec};
use sandbox_protocol::Response;

pub(crate) const OPERATIONS: &[OperationEntry] = &[
    OperationEntry::new(&exec_command::SPEC, exec_command::dispatch),
    OperationEntry::new(&write_command_stdin::SPEC, write_command_stdin::dispatch),
    OperationEntry::new(&poll_command::SPEC, poll_command::dispatch),
    OperationEntry::new(&read_command_lines::SPEC, read_command_lines::dispatch),
    OperationEntry::new(&cancel_command::SPEC, cancel_command::dispatch),
];

pub(crate) const SPECS: &[&OperationSpec] = &[
    &exec_command::SPEC,
    &write_command_stdin::SPEC,
    &poll_command::SPEC,
    &read_command_lines::SPEC,
    &cancel_command::SPEC,
];

pub(super) fn command_yield_response(
    result: Result<CommandYield, CommandServiceError>,
) -> Response {
    match result {
        Ok(output) if output.status == CommandStatus::Running => {
            Response::running(command_yield_value(output))
        }
        Ok(output) => Response::ok(command_yield_value(output)),
        Err(error) => Response::service_error(error),
    }
}

pub(super) fn command_poll_response(
    result: Result<CommandPollOutput, CommandServiceError>,
) -> Response {
    match result {
        Ok(output) if output.status == CommandStatus::Running => {
            Response::running(command_poll_value(output))
        }
        Ok(output) => Response::ok(command_poll_value(output)),
        Err(error) => Response::service_error(error),
    }
}

pub(super) fn command_lines_response(
    result: Result<CommandLinesOutput, CommandServiceError>,
) -> Response {
    match result {
        Ok(output) if output.status == CommandStatus::Running => {
            Response::running(command_lines_value(output))
        }
        Ok(output) => Response::ok(command_lines_value(output)),
        Err(error) => Response::service_error(error),
    }
}

fn command_yield_value(output: CommandYield) -> Value {
    json!({
        "command_session_id": output.command_session_id.map(|command_session_id| command_session_id.0),
        "status": status_name(output.status),
        "exit_code": output.exit_code,
        "output": { "stdout": output.output.stdout },
        "finalized": finalized_value(output.finalized.as_ref()),
    })
}

fn command_poll_value(output: CommandPollOutput) -> Value {
    json!({
        "command_session_id": output.command_session_id.0,
        "status": status_name(output.status),
        "exit_code": output.exit_code,
        "output": { "stdout": output.output.stdout },
        "finalized": finalized_value(output.finalized.as_ref()),
    })
}

fn command_lines_value(output: CommandLinesOutput) -> Value {
    json!({
        "command_session_id": output.command_session_id.0,
        "status": status_name(output.status),
        "exit_code": output.exit_code,
        "start_offset": output.start_offset,
        "end_offset": output.end_offset,
        "total_lines": output.total_lines,
        "truncated_before": output.truncated_before,
        "output_truncated": output.output_truncated,
        "output": output.output.into_iter().map(transcript_row_value).collect::<Vec<_>>(),
    })
}

fn status_name(status: CommandStatus) -> &'static str {
    match status {
        CommandStatus::Running => "running",
        CommandStatus::Completed => "completed",
        CommandStatus::Failed => "failed",
    }
}

fn transcript_row_value(row: CommandTranscriptRow) -> Value {
    json!({
        "offset": row.offset,
        "stream": stream_name(row.stream),
        "text": row.text,
    })
}

fn stream_name(stream: CommandStream) -> &'static str {
    match stream {
        CommandStream::Stdout => "stdout",
        CommandStream::Stderr => "stderr",
    }
}

fn finalized_value(finalized: Option<&CommandFinalizedMetadata>) -> Value {
    finalized.map_or(Value::Null, |finalized| {
        json!({
            "policy": "session",
            "outcome": "session_complete",
            "publish": finalized.publish.as_ref().map(|publish| {
                json!({
                    "status": publish_status_name(publish.status),
                    "rejection": publish.rejection.as_deref().map(publish_reject_value),
                    "revision": publish.revision.as_ref().map(|revision| {
                        json!({
                            "manifest_version": revision.manifest_version,
                            "root_hash": revision.root_hash.as_str(),
                            "layer_count": revision.layer_count,
                        })
                    }),
                    "layer_paths": publish.layer_paths.iter().map(|path| path.to_string_lossy().into_owned()).collect::<Vec<_>>(),
                })
            }),
        })
    })
}

fn publish_status_name(status: CommandPublishStatus) -> &'static str {
    match status {
        CommandPublishStatus::Published => "published",
        CommandPublishStatus::NoOp => "no_op",
        CommandPublishStatus::Rejected => "rejected",
        CommandPublishStatus::Skipped => "skipped",
    }
}

fn publish_reject_value(rejection: &sandbox_runtime_layerstack::PublishReject) -> Value {
    json!({
        "path": rejection.path.as_ref().map(ToString::to_string),
        "reason": format!("{:?}", rejection.reason),
        "source_conflict": rejection.source_conflict.as_ref().map(|conflict| {
            json!({
                "path": conflict.path.to_string(),
                "expected": format!("{:?}", conflict.expected),
                "actual": format!("{:?}", conflict.actual),
            })
        }),
        "protected_drop": rejection.protected_drop.as_ref().map(|drop| {
            json!({
                "path": drop.path.as_str(),
                "reason": format!("{:?}", drop.reason),
            })
        }),
        "message": rejection.message.as_deref(),
    })
}
