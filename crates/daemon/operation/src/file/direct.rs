use std::path::PathBuf;
use std::time::Instant;

use layerstack::{
    hash_current, require_workspace_binding, service, ChangesetResult, CommitOptions, LayerChange,
    LayerPath, LayerStack, LayerStackError, WorkspaceBinding,
};
use serde_json::json;

use super::{
    ChangedPathKind, ChangedPathKinds, FileBackend, FileOpsError, Mutation, MutationCore,
    MutationKind, MutationOutcome, MutationSource, MutationStatus, ReadBytes,
    ResolvedWorkspacePath, WorkspaceConflict, WorkspaceKind, WorkspaceTimings,
};

#[derive(Debug, Clone)]
pub struct DirectBackend {
    root: PathBuf,
    commit_options: CommitOptions,
}

impl DirectBackend {
    #[must_use]
    pub fn new(root: PathBuf) -> Self {
        Self::with_commit_options(root, CommitOptions::default())
    }

    #[must_use]
    pub fn with_commit_options(root: PathBuf, commit_options: CommitOptions) -> Self {
        Self {
            root,
            commit_options,
        }
    }
}

impl FileBackend for DirectBackend {
    fn workspace_kind(&self) -> WorkspaceKind {
        WorkspaceKind::Ephemeral
    }

    fn mutation_source(&self, kind: MutationKind) -> MutationSource {
        match kind {
            MutationKind::Write => MutationSource::DirectWrite,
            MutationKind::Edit => MutationSource::DirectEdit,
        }
    }

    fn resolve_path(&self, request_path: &str) -> Result<ResolvedWorkspacePath, FileOpsError> {
        let binding = require_workspace_binding(&self.root).map_err(api_error)?;
        resolve_layer_path(&binding, request_path)
    }

    fn read_bytes(
        &self,
        path: &ResolvedWorkspacePath,
        max_bytes: usize,
    ) -> Result<ReadBytes, FileOpsError> {
        let stack = LayerStack::open(self.root.clone()).map_err(api_error)?;
        let read_start = Instant::now();
        let (bytes, exists) = stack
            .read_bytes_limited(&path.path, max_bytes)
            .map_err(read_error)?;
        let manifest = stack.read_active_manifest().map_err(api_error)?;
        let mut timings = WorkspaceTimings::new();
        timings.insert(
            "sandbox.file.read.layer_stack_read_s".to_owned(),
            json!(read_start.elapsed().as_secs_f64()),
        );
        Ok(ReadBytes {
            bytes,
            exists,
            manifest_version: Some(manifest.version),
            timings,
        })
    }

    fn apply(&self, mutation: Mutation) -> Result<MutationOutcome, FileOpsError> {
        let path = parse_layer_path(&mutation.path.path)?;
        let base_hash = hash_current(mutation.base.bytes.as_deref(), mutation.base.exists);
        let snapshot_version = mutation
            .base
            .manifest_version
            .map(service::manifest_version_u64)
            .transpose()
            .map_err(api_error)?;
        let occ_start = Instant::now();
        let result = service::commit_direct_with_options(
            &self.root,
            snapshot_version,
            &[LayerChange::Write {
                path: path.clone(),
                content: mutation.content,
            }],
            &[(path, base_hash)],
            self.commit_options,
        )
        .map_err(api_error)?;
        let mut timings = WorkspaceTimings::new();
        timings.insert(
            format!("sandbox.file.{}.occ_apply_s", mutation.kind.verb()),
            json!(occ_start.elapsed().as_secs_f64()),
        );
        Ok(changeset_outcome(
            self.mutation_source(mutation.kind),
            &result,
            timings,
        ))
    }
}

fn changeset_outcome(
    mutation_source: MutationSource,
    result: &ChangesetResult,
    mut timings: WorkspaceTimings,
) -> MutationOutcome {
    for (key, value) in &result.timings {
        timings.insert(key.clone(), json!(value));
    }
    let changed_paths = result.published_paths();
    let changed_path_kinds = changed_paths
        .iter()
        .map(|path| (path.clone(), ChangedPathKind::Write))
        .collect::<ChangedPathKinds>();
    let conflict = result.first_conflict();
    MutationOutcome {
        core: MutationCore {
            success: result.success(),
            conflict: conflict.map(|file| {
                let reason = file.status.wire_str();
                WorkspaceConflict::path(reason, file.path.as_str(), file.conflict_message(reason))
            }),
            conflict_reason: conflict
                .map(|file| file.conflict_message(file.status.wire_str()).to_owned()),
            changed_paths,
            changed_path_kinds,
            mutation_source: Some(mutation_source),
            timings,
        },
        workspace_kind: WorkspaceKind::Ephemeral,
        published: result.success(),
        status: conflict.map_or(MutationStatus::Committed, |file| file.status.into()),
        trace_events: result.trace_events(),
        ..MutationOutcome::default()
    }
}

pub(crate) fn resolve_layer_path(
    binding: &WorkspaceBinding,
    request_path: &str,
) -> Result<ResolvedWorkspacePath, FileOpsError> {
    let path = if request_path.starts_with('/') {
        binding
            .layer_path_from_absolute(request_path)
            .map_err(api_error)?
    } else {
        binding
            .layer_path_from_relative(request_path)
            .map_err(api_error)?
    };
    Ok(ResolvedWorkspacePath::new(path))
}

pub(crate) fn parse_layer_path(raw: &str) -> Result<LayerPath, FileOpsError> {
    LayerPath::parse(raw)
        .map_err(layerstack::LayerStackError::from)
        .map_err(api_error)
}

pub(crate) fn api_error(error: impl std::fmt::Display) -> FileOpsError {
    FileOpsError::new("daemon_workspace_error", error.to_string())
}

pub(crate) fn read_error(error: LayerStackError) -> FileOpsError {
    match error {
        LayerStackError::FileTooLarge { .. } => FileOpsError::invalid_request(error.to_string()),
        other => api_error(other),
    }
}
