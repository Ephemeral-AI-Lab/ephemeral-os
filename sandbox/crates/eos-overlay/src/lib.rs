//! Overlay kernel-mount path and upper-dir capture.
//!
//! # Invariant
//!
//! **Capture + publish is ONE atomic unit per op.** The write set for an
//! operation is captured by walking ONLY the overlay `upperdir` (never the
//! lower layers); other agents never observe a partial write set. The overlay
//! mount itself is built with the RAW new-mount API
//! (`fsopen`/`fsconfig`/`fsmount`/`move_mount`) — NOT the `mount(8)` binary.
//!
//! This is a one-way leaf below occ: overlay has NO `eos-occ` dependency, so the
//! `occ → overlay` edge is acyclic. The `OverlayPathChange ->
//! eos_protocol::LayerChange` conversion lives here precisely because occ
//! consumes it one-way.
//!
//! # Build-time guarantee / platform
//!
//! Syscall crate — `unsafe` is permitted for the raw mount API; every block
//! carries a `// SAFETY:` note and `unsafe_op_in_unsafe_fn` is denied. The
//! syscall surface is Linux-only: every mount/unmount body is gated behind
//! `#[cfg(target_os = "linux")]`, with a `#[cfg(not(target_os = "linux"))]` arm
//! returning [`OverlayError::Unsupported`] so `cargo check` is green on the
//! macOS dev host.
//!
#![deny(unsafe_op_in_unsafe_fn)]

pub mod error;
pub mod kernel_mount;
pub mod path_change;
pub mod writable_dirs;

pub use error::{OverlayError, Result};
pub use kernel_mount::{mount_overlay, unmount_overlay, OverlayHandle, OverlayMount};
pub use path_change::{capture_upperdir, OverlayPathChange, OverlayPathChangeKind};
pub use writable_dirs::{
    allocate_overlay_writable_dirs, overlay_writable_root, OverlayWritableDirs,
    OVERLAY_WRITABLE_ROOT,
};
