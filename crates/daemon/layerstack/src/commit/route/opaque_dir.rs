use std::path::Path;

use crate::model::LayerPath;
use crate::opaque_hidden::{visible_paths_hidden_by_opaque_dir, OpaqueDirExpansion};
use crate::{Manifest, MergedView};

use super::super::error::CommitError;
use super::ignore::IgnoreSource;
use super::model::{
    publish_decision, rejected_drop_decision, PublishDecision, Route, RouteDropReason,
};
use super::protected_paths::{
    is_git_metadata_path, route_decision_for_path_from_source, route_for_path_from_source,
};
use super::snapshot::snapshot_base_hash_for_path;

pub(crate) fn publish_decision_for_opaque_dir(
    root: &Path,
    source: &impl IgnoreSource,
    view: &MergedView,
    manifest: &Manifest,
    path: &LayerPath,
    expansion_limit: usize,
) -> Result<PublishDecision, CommitError> {
    if is_git_metadata_path(path) {
        return Ok(rejected_drop_decision(
            path.clone(),
            RouteDropReason::GitMetadataOpaqueReplace,
        ));
    }

    let hidden = match visible_paths_hidden_by_opaque_dir(root, manifest, path, expansion_limit)
        .map_err(|err| CommitError::RoutePreparation(err.to_string()))?
    {
        OpaqueDirExpansion::Complete(paths) => paths,
        OpaqueDirExpansion::LimitExceeded => {
            return Ok(rejected_drop_decision(
                path.clone(),
                RouteDropReason::OpaqueDirExpansionLimit,
            ));
        }
    };

    if hidden.is_empty() {
        let (route, drop_reason) = route_decision_for_path_from_source(source, path)
            .map_err(|err| CommitError::RoutePreparation(err.to_string()))?;
        return Ok(if route == Route::Gated {
            PublishDecision::gated_paths(path.clone(), Vec::new())
        } else {
            publish_decision(path.clone(), route, None, drop_reason)
        });
    }

    let mut gated_paths = Vec::new();
    let mut direct_paths = Vec::new();
    for hidden_path in &hidden {
        match route_for_path_from_source(source, hidden_path)
            .map_err(|err| CommitError::RoutePreparation(err.to_string()))?
        {
            Route::Drop => {
                return Ok(rejected_drop_decision(
                    path.clone(),
                    RouteDropReason::OpaqueDirProtectedDescendant,
                ));
            }
            Route::Gated => gated_paths.push(hidden_path.clone()),
            Route::Direct => direct_paths.push(hidden_path.clone()),
        }
    }

    if !gated_paths.is_empty() && !direct_paths.is_empty() {
        return Ok(rejected_drop_decision(
            path.clone(),
            RouteDropReason::OpaqueDirMixedRoutes,
        ));
    }

    if !direct_paths.is_empty() {
        return Ok(publish_decision(path.clone(), Route::Direct, None, None));
    }

    let validation_base_hashes = gated_paths
        .iter()
        .map(|hidden_path| {
            Ok((
                hidden_path.clone(),
                snapshot_base_hash_for_path(view, manifest, hidden_path)?,
            ))
        })
        .collect::<Result<Vec<_>, CommitError>>()?;
    Ok(PublishDecision::gated_paths(
        path.clone(),
        validation_base_hashes,
    ))
}
