#![forbid(unsafe_code)]

mod commit;
mod error;
pub(crate) mod fs;
pub(crate) mod lock;
mod model;
pub mod service;
mod squash;
mod stack;
#[cfg(test)]
#[path = "../tests/unit/test_fixture.rs"]
mod test_fixture;
mod workspace;

pub use model::{
    aggregate_layer_changes, layer_digest, manifest_root_hash, CasError, LayerChange, LayerPath,
    LayerRef, Manifest, MANIFEST_SCHEMA_VERSION,
};

pub use commit::{
    configure_auto_squash_max_depth, hash_current, ChangesetResult, CommitError, CommitStatus,
    FileResult, Route,
};
pub use error::LayerStackError;
pub use stack::{LayerStack, Lease, MergedView};
pub use workspace::{
    build_workspace_base, ensure_workspace_base, read_workspace_binding, require_workspace_binding,
    WorkspaceBinding, WORKSPACE_BINDING_FILE,
};

pub(crate) const AUTO_SQUASH_MAX_DEPTH: usize = 100;

pub(crate) const LAYERS_DIR: &str = "layers";

pub(crate) const STAGING_DIR: &str = "staging";

pub const ACTIVE_MANIFEST_FILE: &str = "manifest.json";

pub(crate) const LAYER_METADATA_DIR: &str = ".layer-metadata";
