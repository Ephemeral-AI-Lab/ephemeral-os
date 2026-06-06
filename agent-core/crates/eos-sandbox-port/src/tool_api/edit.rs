//! Pure `edit_file` helper, including the recoverable edit-conflict mapping: a
//! classified conflict transport error becomes `Ok(result{success:false,…})`
//! rather than `Err` (invariant 4). Any other transport error propagates.

use eos_types::SandboxId;
use serde_json::Value;

use crate::error::SandboxPortError;
use crate::models::{ConflictInfo, EditFileRequest, EditFileResult, SandboxResultBase, Workspace};
use crate::ops::DaemonOp;
use crate::timeouts::EDIT_FILE_TIMEOUT_S;
use crate::tool_api::parse::{
    daemon_request_identity_fields, is_edit_conflict, parse_edit_file_result,
    user_visible_error_message,
};
use crate::transport::SandboxTransport;

/// Apply search/replace edits through sandbox-local OCC.
pub async fn edit_file(
    transport: &dyn SandboxTransport,
    sandbox_id: &SandboxId,
    request: &EditFileRequest,
) -> Result<EditFileResult, SandboxPortError> {
    let mut payload = daemon_request_identity_fields(&request.base);
    payload.insert("path".to_owned(), Value::String(request.path.clone()));
    let edits: Vec<Value> = request
        .edits
        .iter()
        .map(|edit| {
            serde_json::json!({
                "old_text": edit.old_text,
                "new_text": edit.new_text,
                "replace_all": edit.replace_all,
            })
        })
        .collect();
    payload.insert("edits".to_owned(), Value::Array(edits));
    payload.insert(
        "description".to_owned(),
        Value::String(
            request
                .base
                .description_or(&format!("edit {}", request.path)),
        ),
    );

    match transport
        .call(sandbox_id, DaemonOp::EditFile, payload, EDIT_FILE_TIMEOUT_S)
        .await
    {
        Ok(response) => parse_edit_file_result(&response),
        Err(error) => match edit_conflict_result(&error, request) {
            Some(result) => Ok(result),
            None => Err(error),
        },
    }
}

/// Map a classified edit-conflict transport error into a recoverable result
/// (mirrors `edit.py::_conflict_from_error`); `None` for any other error.
fn edit_conflict_result(
    error: &SandboxPortError,
    request: &EditFileRequest,
) -> Option<EditFileResult> {
    if !is_edit_conflict(error) {
        return None;
    }
    let message = user_visible_error_message(error.message()).to_owned();
    Some(EditFileResult {
        base: SandboxResultBase {
            success: false,
            workspace: Workspace::Ephemeral,
            timings: Default::default(),
            conflict: Some(ConflictInfo::overlap(request.path.clone(), message.clone())),
            conflict_reason: Some(message),
            changed_paths: vec![request.path.clone()],
            error: None,
        },
        changed_path_kinds: Default::default(),
        mutation_source: String::new(),
        status: "aborted_overlap".to_owned(),
        applied_edits: 0,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::{SandboxRequestBase, SearchReplaceEdit};
    use crate::transport::mock::MockTransport;

    fn request() -> EditFileRequest {
        EditFileRequest {
            base: SandboxRequestBase {
                caller_id: "agent-1".to_owned(),
                description: String::new(),
                invocation_id: None,
            },
            path: "a.txt".to_owned(),
            edits: vec![SearchReplaceEdit {
                old_text: "x".to_owned(),
                new_text: "y".to_owned(),
                replace_all: false,
            }],
        }
    }

    // AC-sandbox-api-05: a classified conflict transport error returns Ok with a
    // success:false conflict result.
    #[tokio::test]
    async fn edit_conflict_error_maps_to_ok_result() {
        let transport = MockTransport::err(SandboxPortError::transport(
            Some("aborted_overlap".to_owned()),
            "internal_error: anchor not found",
        ));
        let sandbox: SandboxId = "sandbox-1".parse().expect("non-empty");
        let result = edit_file(&transport, &sandbox, &request())
            .await
            .expect("conflict maps to Ok");
        assert!(!result.base.success);
        assert_eq!(result.status, "aborted_overlap");
        assert_eq!(result.base.changed_paths, vec!["a.txt"]);
        let conflict = result.base.conflict.expect("conflict");
        assert_eq!(conflict.reason, "aborted_overlap");
        assert_eq!(conflict.conflict_file.as_deref(), Some("a.txt"));
        // The conflict message is the prefix-stripped user-visible message.
        assert_eq!(conflict.message, "anchor not found");
        assert_eq!(
            result.base.conflict_reason.as_deref(),
            Some("anchor not found")
        );
    }

    // AC-sandbox-api-05: a non-conflict transport error propagates as Err.
    #[tokio::test]
    async fn edit_non_conflict_error_propagates() {
        let transport = MockTransport::err(SandboxPortError::transport(
            Some("boom".to_owned()),
            "unexpected failure",
        ));
        let sandbox: SandboxId = "sandbox-1".parse().expect("non-empty");
        assert!(edit_file(&transport, &sandbox, &request()).await.is_err());
    }
}
