use crate::model::LayerPath;

use super::model::PublishRejectReason;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum RouteKind {
    Source,
    Ignored,
}

pub(crate) fn forbidden_path(path: &LayerPath) -> Option<PublishRejectReason> {
    is_protected(path.as_str()).then_some(PublishRejectReason::ProtectedPath)
}

/// Reserved layerstack-internal namespace: the top-level entries
/// `manifest.json`, `workspace.json`, `layers`, `staging`, and
/// `.layer-metadata`; any `.layer-metadata` path component; and any path
/// component beginning with `.wh.` (including the bare `.wh.` and the opaque
/// marker `.wh..wh..opq`), which collides with the overlay/OCI whiteout
/// marker encoding layerstack uses inside layer directories. Lookalikes
/// without the trailing dot (`.wh`, `.whx`, `x.wh.y`) are ordinary paths.
fn is_protected(path: &str) -> bool {
    let mut parts = path.split('/');
    let first = parts.next().unwrap_or_default();
    if matches!(
        first,
        "manifest.json" | "workspace.json" | "layers" | "staging" | ".layer-metadata"
    ) {
        return true;
    }
    path.split('/')
        .any(|part| part == ".layer-metadata" || part.starts_with(".wh."))
}
