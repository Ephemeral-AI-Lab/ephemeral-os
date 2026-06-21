use crate::error::LayerStackError;
use crate::model::{LayerPath, Manifest};
use crate::stack::MergedView;

pub(crate) const OPAQUE_DIR_EXPANSION_LIMIT: usize = 4096;

pub(crate) fn hidden_descendants(
    view: &MergedView,
    manifest: &Manifest,
    path: &LayerPath,
) -> Result<Vec<LayerPath>, LayerStackError> {
    view.visible_descendants(path, manifest, OPAQUE_DIR_EXPANSION_LIMIT)
}
