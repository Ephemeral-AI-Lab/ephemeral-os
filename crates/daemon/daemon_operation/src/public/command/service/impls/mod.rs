mod cancel;
mod exec_command;
mod poll;
mod read_command_lines;
mod write_command_stdin;

use serde_json::{json, Map, Value};

use crate::command::{
    CommandFinalizationOutcome, CommandFinalizedMetadata, CommandFinalizedPolicy,
    CommandLinesOutput, CommandPollOutput, CommandServiceError, CommandStatus, CommandStream,
    CommandTranscriptRow, CommandWorkspaceDestroyMetadata, CommandYield,
};
use crate::operation::{OperationEntry, OperationRequest, OperationResponse, OperationSpec};

pub(crate) const OPERATIONS: &[OperationEntry] = &[
    OperationEntry::new(&exec_command::SPEC, exec_command::dispatch),
    OperationEntry::new(&write_command_stdin::SPEC, write_command_stdin::dispatch),
    OperationEntry::new(&poll::SPEC, poll::dispatch),
    OperationEntry::new(&read_command_lines::SPEC, read_command_lines::dispatch),
    OperationEntry::new(&cancel::SPEC, cancel::dispatch),
];

pub(crate) const SPECS: &[&OperationSpec] = &[
    &exec_command::SPEC,
    &write_command_stdin::SPEC,
    &poll::SPEC,
    &read_command_lines::SPEC,
    &cancel::SPEC,
];

pub(super) fn command_yield_response(
    request: &OperationRequest<'_>,
    result: Result<CommandYield, CommandServiceError>,
) -> OperationResponse {
    match result {
        Ok(output) if output.status == CommandStatus::Running => {
            OperationResponse::running(request, command_yield_value(output))
        }
        Ok(output) => OperationResponse::ok(request, command_yield_value(output)),
        Err(error) => OperationResponse::service_error(request, error),
    }
}

pub(super) fn command_poll_response(
    request: &OperationRequest<'_>,
    result: Result<CommandPollOutput, CommandServiceError>,
) -> OperationResponse {
    match result {
        Ok(output) if output.status == CommandStatus::Running => {
            OperationResponse::running(request, command_poll_value(output))
        }
        Ok(output) => OperationResponse::ok(request, command_poll_value(output)),
        Err(error) => OperationResponse::service_error(request, error),
    }
}

pub(super) fn command_lines_response(
    request: &OperationRequest<'_>,
    result: Result<CommandLinesOutput, CommandServiceError>,
) -> OperationResponse {
    match result {
        Ok(output) if output.status == CommandStatus::Running => {
            OperationResponse::running(request, command_lines_value(output))
        }
        Ok(output) => OperationResponse::ok(request, command_lines_value(output)),
        Err(error) => OperationResponse::service_error(request, error),
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
    finalized.map_or(Value::Null, |metadata| {
        json!({
            "policy": finalized_policy_name(metadata.policy),
            "outcome": finalization_outcome_name(metadata.outcome),
            "changed_paths": &metadata.changed_paths,
            "changed_path_kinds": changed_path_kinds_value(metadata),
            "protected_drop_count": metadata.protected_drop_count,
            "captured_change_count": metadata.captured_change_count,
            "metadata_path_count": metadata.metadata_path_count,
            "published_manifest_version": metadata.published_manifest_version,
            "destroy": destroy_value(metadata.destroy.as_ref()),
        })
    })
}

fn finalized_policy_name(policy: CommandFinalizedPolicy) -> &'static str {
    match policy {
        CommandFinalizedPolicy::Session => "session",
        CommandFinalizedPolicy::OneShotPublishThenDestroy => "one_shot_publish_then_destroy",
    }
}

fn finalization_outcome_name(outcome: CommandFinalizationOutcome) -> &'static str {
    match outcome {
        CommandFinalizationOutcome::SessionComplete => "session_complete",
        CommandFinalizationOutcome::Published => "published",
        CommandFinalizationOutcome::Discarded => "discarded",
    }
}

fn changed_path_kinds_value(metadata: &CommandFinalizedMetadata) -> Value {
    metadata
        .changed_path_kinds
        .iter()
        .map(|(path, kind)| (path.clone(), Value::String(format!("{kind:?}"))))
        .collect::<Map<_, _>>()
        .into()
}

fn destroy_value(destroy: Option<&CommandWorkspaceDestroyMetadata>) -> Value {
    destroy.map_or(Value::Null, |destroy| {
        json!({
            "evicted_upperdir_bytes": destroy.evicted_upperdir_bytes,
            "lease_released": destroy.lease_released,
            "lease_release_error": &destroy.lease_release_error,
            "active_leases_after": destroy.active_leases_after,
        })
    })
}
