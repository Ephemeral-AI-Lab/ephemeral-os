//! Workspace file-operation contracts (transitional).
//!
//! What remains of this crate after the tool-call-centric split: the shared
//! file-op contract (`contract`) and the per-mode `WorkspaceFileOps`
//! implementations the daemon still drives. The command tier lives in
//! `eos-command-ops`; the workspaces live in `eos-ephemeral-workspace` /
//! `eos-isolated-workspace`. This crate is deleted when `eos-file-ops` takes
//! over the file tool family.
#![forbid(unsafe_code)]

pub mod contract;
pub mod ephemeral;
pub mod isolated;
