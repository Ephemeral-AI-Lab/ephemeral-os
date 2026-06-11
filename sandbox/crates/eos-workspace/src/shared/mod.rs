//! Contracts shared by ephemeral and isolated workspace modes.

mod dirs;
mod timing;
mod tree;

pub use dirs::{allocate_overlay_dirs, overlay_run_dirs, DirAllocationError, OverlayDirs};
pub use timing::record_phase_ms;
pub use tree::{directory_file_bytes, path_changes_to_wire, TreeResourceStats};

pub use dirs::OverlayDirsGuard;
