use crate::commit::git_metadata::parts_after_git_dir;
use crate::model::LayerPath;
use crate::LayerStackError;

use super::ignore::{path_is_ignored, IgnoreSource};
use super::model::{Route, RouteDropReason};

pub(super) fn route_for_path_from_source(
    source: &impl IgnoreSource,
    path: &LayerPath,
) -> Result<Route, LayerStackError> {
    Ok(route_decision_for_path_from_source(source, path)?.0)
}

pub(crate) fn route_decision_for_path_from_source(
    source: &impl IgnoreSource,
    path: &LayerPath,
) -> Result<(Route, Option<RouteDropReason>), LayerStackError> {
    if is_git_metadata_path(path) {
        return Ok((Route::Drop, Some(RouteDropReason::GitMetadataUnsupported)));
    }
    if let Some(reason) = protected_path_drop_reason(path) {
        return Ok((Route::Drop, Some(reason)));
    }
    if path_is_ignored(source, path.as_str())? {
        Ok((Route::Direct, None))
    } else {
        Ok((Route::Gated, None))
    }
}

pub(crate) fn is_git_metadata_path(path: &LayerPath) -> bool {
    parts_after_git_dir(path).is_some()
}

fn protected_path_drop_reason(path: &LayerPath) -> Option<RouteDropReason> {
    let path = path.as_str();
    let mut parts = path.split('/');
    let first = parts.next()?;
    if matches!(
        first,
        "manifest.json" | "workspace.json" | "layers" | "staging"
    ) || first == ".layer-metadata"
        || parts.any(|part| part == ".layer-metadata")
    {
        return Some(RouteDropReason::DaemonControlPath);
    }
    if is_command_scratch_path(path) {
        return Some(RouteDropReason::CommandScratchPath);
    }
    None
}

fn is_command_scratch_path(path: &str) -> bool {
    matches!(
        path,
        "command-request.json" | "runner-result.json" | "final.json" | "transcript.log"
    )
}
