use std::time::Instant;

use eos_workspace_api::{
    WorkspaceApiError, WorkspaceMutationKind, WorkspaceMutationRequest, WorkspaceMutationSink,
    WorkspaceReadView, WriteFileOutcome, WriteFileRequest,
};

use super::response;

pub(super) fn write_file<P>(
    ports: &P,
    request: WriteFileRequest,
) -> Result<WriteFileOutcome, WorkspaceApiError>
where
    P: WorkspaceReadView + WorkspaceMutationSink,
{
    let total_start = Instant::now();
    if request.content.len() > request.max_file_bytes {
        return Err(WorkspaceApiError::invalid_request(format!(
            "file too large: {} > {} bytes",
            request.content.len(),
            request.max_file_bytes
        )));
    }
    let path = ports.resolve_path(&request.path)?;
    let base = ports.read_bytes(&path)?;
    if !request.overwrite && base.exists {
        let mut timings = base.timings;
        response::insert_total(&mut timings, "write", total_start);
        return Ok(response::write_conflict(
            &path.path,
            "rejected",
            "create_only_existing",
            "file already exists",
            timings,
        ));
    }
    let mut outcome = ports.commit_or_record(WorkspaceMutationRequest {
        kind: WorkspaceMutationKind::Write,
        path,
        content: request.content,
        base,
    })?;
    response::insert_total(&mut outcome.timings, "write", total_start);
    Ok(outcome.into())
}
