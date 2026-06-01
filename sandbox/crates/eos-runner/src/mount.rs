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

/// Validated overlay-mount inputs (newest-first lower layers + upper/work dirs),
/// the runner's mirror of the Python `MountInputs` the entrypoint builds.
/// `// PORT backend/src/sandbox/overlay/kernel_mount.py — MountInputs / validate_mount_inputs`
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MountInputs {
    /// Workspace root the overlay is moved onto (e.g. `/testbed`). `move_mount`
    /// rejects a `/proc/self/fd` symlink destination.
    /// `// PORT backend/src/sandbox/overlay/kernel_mount.py:149 — move_mount dest constraint`
    pub workspace_root: PathBuf,
    /// Lower layer paths, **newest-first** — the order is hashed into the
    /// `lowerdir+` fsconfig sequence and is load-bearing.
    /// `// PORT backend/src/sandbox/overlay/kernel_mount.py:6,65 — lowerdir+ newest-first`
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
/// `// PORT backend/src/sandbox/overlay/kernel_mount.py:63-70 — mount_overlay raw new-mount API`
pub trait MountedOverlay: Debug {}

impl<T: Debug> MountedOverlay for T {}

pub trait KernelMountPort {
    /// Mount the overlay described by `inputs` at its workspace root. Must be
    /// called on a caller that already holds `CAP_SYS_ADMIN` in the target
    /// mount namespace (post-unshare / post-setns).
    fn mount_overlay(&self, inputs: &MountInputs) -> Result<Box<dyn MountedOverlay>, RunnerError>;
}
