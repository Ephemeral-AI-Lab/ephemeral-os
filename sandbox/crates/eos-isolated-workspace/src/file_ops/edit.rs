use std::time::Instant;

use eos_workspace_api::{
    file_ops::apply_search_replace, EditFileOutcome, EditFileRequest, SearchReplaceError,
    WorkspaceApiError, WorkspaceMutationKind, WorkspaceMutationRequest, WorkspaceMutationSink,
    WorkspaceReadView,
};

use super::response;

pub(super) fn edit_file<P>(
    ports: &P,
    request: EditFileRequest,
) -> Result<EditFileOutcome, WorkspaceApiError>
where
    P: WorkspaceReadView + WorkspaceMutationSink,
{
    let total_start = Instant::now();
    let path = ports.resolve_path(&request.path)?;
    let base = ports.read_bytes(&path)?;
    if !base.exists {
        let mut timings = base.timings;
        response::insert_total(&mut timings, "edit", total_start);
        return Ok(response::edit_conflict(
            &path.path,
            "aborted_version",
            "aborted_version",
            "file does not exist",
            timings,
        ));
    }
    let bytes = base.bytes.clone().unwrap_or_default();
    let mut content = String::from_utf8(bytes).map_err(|err| {
        WorkspaceApiError::invalid_request(format!("file is not utf-8 text: {err}"))
    })?;
    for edit in &request.edits {
        if edit.old_text.is_empty() {
            return Err(WorkspaceApiError::invalid_request(
                "edit anchor old_text must be non-empty",
            ));
        }
        match apply_search_replace(&content, &edit.old_text, &edit.new_text, edit.replace_all) {
            Ok(next) => content = next,
            Err(err) => {
                let mut timings = base.timings;
                response::insert_total(&mut timings, "edit", total_start);
                return Ok(response::edit_conflict(
                    &path.path,
                    "aborted_overlap",
                    "aborted_overlap",
                    search_replace_message(&err),
                    timings,
                ));
            }
        }
    }
    let mut outcome = ports.commit_or_record(WorkspaceMutationRequest {
        kind: WorkspaceMutationKind::Edit,
        path,
        content: content.into_bytes(),
        base,
    })?;
    response::insert_total(&mut outcome.timings, "edit", total_start);
    Ok(EditFileOutcome::from_mutation(
        outcome,
        i64::try_from(request.edits.len()).unwrap_or(i64::MAX),
    ))
}

const fn search_replace_message(err: &SearchReplaceError) -> &'static str {
    match err {
        SearchReplaceError::EmptyAnchor => "edit anchor old_text must be non-empty",
        SearchReplaceError::NotFound => "anchor not found",
        SearchReplaceError::CountMismatch => "anchor occurrence count mismatch",
        _ => "search/replace failed",
    }
}
