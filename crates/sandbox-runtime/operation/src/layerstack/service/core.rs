use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use sandbox_observability::Observer;

use crate::file::FileService;
use crate::layerstack::LayerStackServiceError;

pub(crate) const EXPORT_SPOOL_DIR: &str = ".export";

/// One paged export spool: the on-disk `tar.zst` and its byte total. The
/// registry is in-memory by design — a daemon restart drops it and paging
/// aborts with export-not-found; re-running the export is the recovery.
pub(crate) struct ExportSpool {
    pub(crate) path: PathBuf,
    pub(crate) total: u64,
}

pub struct LayerStackService {
    pub(crate) layer_stack_root: PathBuf,
    pub(crate) scratch_root: PathBuf,
    pub(crate) obs: Observer,
    pub(crate) file: Arc<FileService>,
    pub(crate) audit_gate: Mutex<()>,
    pub(crate) export_spools: Mutex<HashMap<String, ExportSpool>>,
}

impl LayerStackService {
    pub fn new(
        layer_stack_root: PathBuf,
        scratch_root: PathBuf,
        obs: Observer,
        file: Arc<FileService>,
    ) -> Result<Self, LayerStackServiceError> {
        sandbox_runtime_layerstack::require_workspace_binding(&layer_stack_root).map_err(
            |error| LayerStackServiceError::Init {
                layer_stack_root: layer_stack_root.clone(),
                error: error.to_string(),
            },
        )?;
        Ok(Self {
            layer_stack_root,
            scratch_root,
            obs,
            file,
            audit_gate: Mutex::new(()),
            export_spools: Mutex::new(HashMap::new()),
        })
    }

    #[must_use]
    pub fn layer_stack_root(&self) -> &std::path::Path {
        &self.layer_stack_root
    }

    #[must_use]
    pub(crate) fn export_spool_dir(&self) -> PathBuf {
        self.scratch_root.join(EXPORT_SPOOL_DIR)
    }
}
