use std::path::Path;

use crate::{LayerStack, LayerStackError, Lease};

pub fn acquire_snapshot_with_lease(
    root: &Path,
    request_id: &str,
) -> Result<Lease, LayerStackError> {
    LayerStack::open(root.to_path_buf())?.acquire_snapshot(request_id)
}
