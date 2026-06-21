use std::path::PathBuf;

use crate::Manifest;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Snapshot {
    pub manifest_version: i64,
    pub root_hash: String,
    pub layer_paths: Vec<PathBuf>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LeasedSnapshot {
    pub lease_id: String,
    pub manifest_version: i64,
    pub root_hash: String,
    pub manifest: Manifest,
    pub layer_paths: Vec<PathBuf>,
}
