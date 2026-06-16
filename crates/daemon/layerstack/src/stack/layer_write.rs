use std::path::Path;

use crate::error::LayerStackError;
use crate::fs::{join_layer_path, remove_path};
use crate::model::{aggregate_layer_changes, LayerChange};

use super::whiteout::{write_kernel_whiteout, OPAQUE_MARKER};

pub(super) fn write_layer_changes(
    layer_dir: &Path,
    changes: &[LayerChange],
) -> Result<(), LayerStackError> {
    for change in aggregate_layer_changes(changes) {
        match change {
            LayerChange::Write { path, content } => {
                let target = join_layer_path(layer_dir, path.as_str());
                if let Some(parent) = target.parent() {
                    std::fs::create_dir_all(parent)?;
                }
                remove_path(&target)?;
                std::fs::write(target, content)?;
            }
            LayerChange::WriteFile {
                path,
                source_path,
                size,
            } => {
                let target = join_layer_path(layer_dir, path.as_str());
                if let Some(parent) = target.parent() {
                    std::fs::create_dir_all(parent)?;
                }
                remove_path(&target)?;
                let source_meta = std::fs::metadata(&source_path)?;
                if !source_meta.is_file() || source_meta.len() != size {
                    return Err(LayerStackError::Storage(format!(
                        "spool payload changed before publish: {}",
                        source_path.display()
                    )));
                }
                std::fs::copy(source_path, &target)?;
                let target_meta = std::fs::metadata(&target)?;
                if target_meta.len() != size {
                    return Err(LayerStackError::Storage(format!(
                        "spool payload copy size mismatch for {}",
                        path.as_str()
                    )));
                }
            }
            LayerChange::Delete { path } => {
                let target = join_layer_path(layer_dir, path.as_str());
                if let Some(parent) = target.parent() {
                    std::fs::create_dir_all(parent)?;
                }
                remove_path(&target)?;
                write_kernel_whiteout(&target)?;
            }
            LayerChange::Symlink { path, source_path } => {
                let target = join_layer_path(layer_dir, path.as_str());
                if let Some(parent) = target.parent() {
                    std::fs::create_dir_all(parent)?;
                }
                remove_path(&target)?;
                std::os::unix::fs::symlink(source_path, target)?;
            }
            LayerChange::OpaqueDir { path } => {
                let marker = join_layer_path(layer_dir, path.as_str()).join(OPAQUE_MARKER);
                if let Some(parent) = marker.parent() {
                    std::fs::create_dir_all(parent)?;
                }
                std::fs::write(marker, b"")?;
            }
        }
    }
    Ok(())
}
