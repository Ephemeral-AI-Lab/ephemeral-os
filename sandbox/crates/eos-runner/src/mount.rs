//! Overlay-mount port (inversion of the `eos-overlay` `kernel_mount` edge).
//!
//! The runner does not own the raw `fsopen`/`fsconfig`/`fsmount`/`move_mount`
//! sequence — that is `eos-overlay`'s. Fresh-ns and setns-overlay-mount both
//! delegate to it once they are inside the right namespace (the Python helpers
//! `import sandbox.overlay.kernel_mount` *after* the setns/unshare calls, since
//! it transitively pulls `subprocess` which would break the single-thread
//! requirement at module-load time — see
//! `isolated_workspace/scripts/setns_overlay_mount.py:55-73`).
//!
//! The contract is expressed here as a local port trait and input struct so
//! the runner depends only on validated mount inputs; the daemon wires a thin
//! adapter implementing [`KernelMountPort`] over `eos-overlay::kernel_mount`.

use std::fmt::Debug;
use std::path::PathBuf;

use crate::error::RunnerError;

/// Validated overlay-mount inputs.
///
/// This is the runner's mirror of the Python `MountInputs` the entrypoint
/// builds: newest-first lower layers plus upper/work dirs.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MountInputs {
    /// Workspace root the overlay is moved onto. `move_mount`
    /// rejects a `/proc/self/fd` symlink destination.
    pub workspace_root: PathBuf,
    /// Lower layer paths, **newest-first** — the order is hashed into the
    /// `lowerdir+` fsconfig sequence and is load-bearing.
    pub layer_paths: Vec<PathBuf>,
    pub upperdir: PathBuf,
    pub workdir: PathBuf,
}

/// The overlay-mount port the runner calls once inside the target namespace.
///
/// Implemented in `eos-daemon` as an adapter over `eos-overlay::kernel_mount`
/// (`mount_overlay`): `fsopen("overlay")` → per-layer `fsconfig("lowerdir+")` →
/// `fsconfig("upperdir"/"workdir")` → `fsconfig_create` → `fsmount` →
/// `move_mount(workspace_root)`.
pub trait MountedOverlay: Debug {}

impl<T: Debug> MountedOverlay for T {}

pub trait KernelMountPort {
    /// Mount the overlay described by `inputs` at its workspace root. Must be
    /// called on a caller that already holds `CAP_SYS_ADMIN` in the target
    /// mount namespace (post-unshare / post-setns).
    ///
    /// # Errors
    ///
    /// Returns [`RunnerError`] when the kernel overlay mount fails or when the
    /// mount inputs are rejected by the concrete mount port.
    fn mount_overlay(&self, inputs: &MountInputs) -> Result<Box<dyn MountedOverlay>, RunnerError>;
}
