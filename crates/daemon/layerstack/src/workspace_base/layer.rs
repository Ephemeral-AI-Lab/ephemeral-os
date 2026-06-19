use std::io::ErrorKind;
use std::path::Path;

use crate::error::LayerStackError;
use crate::fs::{join_layer_path, remove_path};
use crate::model::LayerRef;
use crate::{LAYERS_DIR, STAGING_DIR};

use super::collect::{file_hash, BaseEntry};

const WORKSPACE_BASE_LAYER_ID: &str = "B000001-base";

pub(super) fn write_base_layer(
    stack: &Path,
    entries: &[BaseEntry],
) -> Result<LayerRef, LayerStackError> {
    let layer_id = WORKSPACE_BASE_LAYER_ID;
    let layer_dir = stack.join(LAYERS_DIR).join(layer_id);
    let staging_dir = stack.join(STAGING_DIR).join(format!("{layer_id}.staging"));
    if layer_dir.exists() || staging_dir.exists() {
        return Err(LayerStackError::Storage(format!(
            "base layer already exists: {}",
            layer_dir.display()
        )));
    }
    std::fs::create_dir_all(&staging_dir)?;
    let result = (|| {
        for entry in entries {
            let target = join_layer_path(&staging_dir, entry.path());
            if let Some(parent) = target.parent() {
                std::fs::create_dir_all(parent)?;
            }
            match entry {
                BaseEntry::Directory { .. } => {
                    std::fs::create_dir_all(&target)?;
                }
                BaseEntry::File {
                    source_path,
                    content_hash,
                    path,
                    ..
                } => {
                    let current_hash = file_hash(source_path).map_err(|err| {
                        if err.kind() == ErrorKind::NotFound {
                            LayerStackError::Storage(format!(
                                "workspace base path changed while copying: {path}"
                            ))
                        } else {
                            err.into()
                        }
                    })?;
                    if &current_hash != content_hash {
                        return Err(LayerStackError::Storage(format!(
                            "workspace base path changed while copying: {path}"
                        )));
                    }
                    remove_path(&target)?;
                    std::fs::copy(source_path, &target)?;
                }
                BaseEntry::Symlink { link_target, .. } => {
                    remove_path(&target)?;
                    std::os::unix::fs::symlink(link_target, &target)?;
                }
            }
        }
        if let Some(parent) = layer_dir.parent() {
            std::fs::create_dir_all(parent)?;
        }
        std::fs::rename(&staging_dir, &layer_dir)?;
        Ok::<(), LayerStackError>(())
    })();
    if let Err(err) = result {
        let _ = remove_path(&staging_dir);
        let _ = remove_path(&layer_dir);
        return Err(err);
    }
    Ok(LayerRef {
        layer_id: layer_id.to_owned(),
        path: format!("{LAYERS_DIR}/{layer_id}"),
    })
}
