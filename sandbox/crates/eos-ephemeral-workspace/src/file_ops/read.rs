use std::time::Instant;

use eos_workspace_api::{ReadFileOutcome, ReadFileRequest, WorkspaceApiError, WorkspaceReadView};

use super::response;

pub(super) fn read_file<P>(
    ports: &P,
    request: ReadFileRequest,
) -> Result<ReadFileOutcome, WorkspaceApiError>
where
    P: WorkspaceReadView,
{
    let total_start = Instant::now();
    let path = ports.resolve_path(&request.path)?;
    let read = ports.read_bytes(&path)?;
    let content = if read.exists {
        let bytes = read.bytes.unwrap_or_default();
        if bytes.len() > request.max_read_bytes {
            return Err(WorkspaceApiError::invalid_request(format!(
                "file too large: {} > {} bytes",
                bytes.len(),
                request.max_read_bytes
            )));
        }
        String::from_utf8_lossy(&bytes).into_owned()
    } else {
        String::new()
    };
    let mut timings = read.timings;
    response::insert_total(&mut timings, "read", total_start);
    Ok(response::read_outcome(content, read.exists, timings))
}
