//! Daemon-side adapter/seam layer.
//!
//! Each submodule binds a sibling-crate contract (`eos-occ`, `eos-overlay`,
//! `eos-plugin`, `eos-checkpoint-host`, `eos-workspace-api`, the workspace-run
//! host) to daemon-owned resources — the OCC single-writer cache, audit ring,
//! resource telemetry, and `ns-holder`/`ns-runner` re-exec. These are seams over
//! those crates, not reimplementations of them.

pub(crate) mod checkpoint;
pub(crate) mod occ;
pub(crate) mod overlay;
pub(crate) mod plugins;
pub(crate) mod workspace;
pub(crate) mod workspace_run;
