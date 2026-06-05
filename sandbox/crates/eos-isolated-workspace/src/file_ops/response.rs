use std::time::Instant;

use eos_workspace_api::{
    ChangedPathKinds, EditFileOutcome, ReadFileOutcome, WorkspaceConflict, WorkspaceMode,
    WorkspaceTimings, WriteFileOutcome,
};
use serde_json::json;

pub(super) const MODE: WorkspaceMode = WorkspaceMode::Isolated;

pub(super) fn insert_total(timings: &mut WorkspaceTimings, verb: &str, start: Instant) {
    timings.insert(
        format!("api.{verb}.total_s"),
        json!(start.elapsed().as_secs_f64()),
    );
}

pub(super) const fn mutation_source() -> &'static str {
    "isolated_workspace"
}

pub(super) fn read_outcome(
    content: String,
    exists: bool,
    timings: WorkspaceTimings,
) -> ReadFileOutcome {
    ReadFileOutcome {
        mode: MODE,
        success: true,
        content,
        exists,
        encoding: "utf-8".to_owned(),
        timings,
    }
}

pub(super) fn write_conflict(
    path: &str,
    status: &str,
    reason: &str,
    message: &str,
    timings: WorkspaceTimings,
) -> WriteFileOutcome {
    WriteFileOutcome {
        mode: MODE,
        success: false,
        status: status.to_owned(),
        conflict: Some(WorkspaceConflict::path(reason, path, message)),
        conflict_reason: Some(reason.to_owned()),
        changed_paths: Vec::new(),
        changed_path_kinds: ChangedPathKinds::new(),
        mutation_source: mutation_source().to_owned(),
        timings,
    }
}

pub(super) fn edit_conflict(
    path: &str,
    status: &str,
    reason: &str,
    message: &str,
    timings: WorkspaceTimings,
) -> EditFileOutcome {
    EditFileOutcome {
        mode: MODE,
        success: false,
        status: status.to_owned(),
        conflict: Some(WorkspaceConflict::path(reason, path, message)),
        conflict_reason: Some(reason.to_owned()),
        changed_paths: Vec::new(),
        changed_path_kinds: ChangedPathKinds::new(),
        mutation_source: mutation_source().to_owned(),
        timings,
        applied_edits: 0,
    }
}
