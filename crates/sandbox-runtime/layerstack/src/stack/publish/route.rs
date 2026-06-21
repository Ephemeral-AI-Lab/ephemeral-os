use crate::model::LayerPath;

use super::model::PublishRejectReason;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum RouteKind {
    Source,
    Ignored,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum ForbiddenRoute {
    GitMutation,
    Protected,
}

pub(crate) fn forbidden_path(path: &LayerPath) -> Option<(PublishRejectReason, ForbiddenRoute)> {
    if has_component(path.as_str(), ".git") {
        return Some((
            PublishRejectReason::GitMutationForbidden,
            ForbiddenRoute::GitMutation,
        ));
    }
    if is_protected(path.as_str()) {
        return Some((
            PublishRejectReason::ProtectedPath,
            ForbiddenRoute::Protected,
        ));
    }
    None
}

pub(crate) fn has_component(path: &str, needle: &str) -> bool {
    path.split('/').any(|part| part == needle)
}

fn is_protected(path: &str) -> bool {
    let mut parts = path.split('/');
    let first = parts.next().unwrap_or_default();
    if matches!(
        first,
        "manifest.json" | "workspace.json" | "layers" | "staging" | ".layer-metadata"
    ) {
        return true;
    }
    path.split('/').any(|part| part == ".layer-metadata")
}
