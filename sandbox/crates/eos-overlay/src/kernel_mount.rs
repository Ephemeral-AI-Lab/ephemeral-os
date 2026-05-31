//! Kernel-boundary overlay mount mechanics — the RAW new-mount API.
//!
//! The overlay is built with `fsopen`/`fsconfig`/`fsmount`/`move_mount` (NOT the
//! `mount(8)` binary). Ordering invariant: the first
//! `fsconfig(SET_STRING, "lowerdir+", path)` call is the highest-priority lower
//! layer, so [`OverlayHandle::layer_paths`] is iterated in its given
//! newest-first order.
//!
//! Linux-only: every syscall body is gated behind `#[cfg(target_os = "linux")]`
//! with a `#[cfg(not(target_os = "linux"))]` arm returning
//! [`OverlayError::Unsupported`] so non-Linux `cargo check` stays green.

use std::path::PathBuf;

#[cfg(target_os = "linux")]
use std::os::fd::RawFd;

use crate::error::{OverlayError, Result};

/// The inputs for one overlay mount.
///
/// `layer_paths` is the leased lower stack in NEWEST-FIRST order (element 0 =
/// highest-priority lower); `upperdir`/`workdir` are the writable side from
/// [`crate::writable_dirs`].
/// `// PORT backend/src/sandbox/overlay/kernel_mount.py:31-42 — MountInputs`
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OverlayHandle {
    /// Writable upper directory.
    pub upperdir: PathBuf,
    /// Overlayfs work directory (sibling of `upperdir`).
    pub workdir: PathBuf,
    /// Leased lower-layer paths, NEWEST-FIRST (mount priority order).
    pub layer_paths: Vec<PathBuf>,
}

/// A live overlay mount at a workspace root. RAII: [`Drop`] unmounts.
///
/// Wraps the `fsmount` file descriptor returned by the new-mount API; the fd is
/// `#[repr(transparent)]` so it crosses the syscall FFI as a bare `RawFd`, and
/// owns its teardown — dropping the handle unmounts the workspace root.
/// `// PORT backend/src/sandbox/overlay/kernel_mount.py:49-75 — mount_overlay (+ umount on teardown)`
#[derive(Debug)]
pub struct OverlayMount {
    /// The mountpoint this overlay was moved onto (`move_mount` destination).
    workspace_root: PathBuf,
    #[cfg(target_os = "linux")]
    mount_fd: MountFd,
}

/// `#[repr(transparent)]` owned mount file descriptor (RAII close on drop).
/// Closing the `fsmount` fd is distinct from unmounting the destination; both
/// are handled on teardown of the owning [`OverlayMount`].
#[cfg(target_os = "linux")]
#[repr(transparent)]
#[derive(Debug)]
struct MountFd(RawFd);

#[cfg(target_os = "linux")]
impl Drop for MountFd {
    fn drop(&mut self) {
        // SAFETY (future): `self.0` is an fd this type uniquely owns (moved out
        // of `fsmount` and never duplicated), so closing it exactly once here
        // is sound. No real close yet — this is a skeleton.
        // PORT backend/src/sandbox/overlay/kernel_mount.py:44-46 — _close_fd(os.close)
        todo!()
    }
}

impl OverlayMount {
    /// The workspace root this overlay is mounted at.
    pub fn workspace_root(&self) -> &std::path::Path {
        &self.workspace_root
    }
}

impl Drop for OverlayMount {
    fn drop(&mut self) {
        // Best-effort: peel every stacked mount at `workspace_root`. A Drop impl
        // cannot return an error, so failures are swallowed (matching the Python
        // non-raising default). Nothing constructs an `OverlayMount` until
        // `mount_overlay` lands, so this never runs in the skeleton.
        // PORT backend/src/sandbox/overlay/kernel_mount.py:78-121 — umount loop on teardown
        todo!()
    }
}

/// Mount an overlay filesystem at `workspace_root` from `handle`.
///
/// Builds the mount via the raw API in this exact order (per the ordering
/// invariant): `fsopen("overlay")`, one `fsconfig_string("lowerdir+", layer)`
/// per layer in `handle.layer_paths` (newest-first), then `"upperdir"`,
/// `"workdir"`, `fsconfig_create`, `fsmount`, and finally `move_mount` onto the
/// real `workspace_root` (NOT a `/proc/self/fd` symlink — `move_mount(2)`
/// rejects that as a destination).
/// `// PORT backend/src/sandbox/overlay/kernel_mount.py:49-75 — mount_overlay`
#[cfg(target_os = "linux")]
pub fn mount_overlay(
    workspace_root: &std::path::Path,
    handle: &OverlayHandle,
) -> Result<OverlayMount> {
    // PORT backend/src/sandbox/overlay/kernel_mount.py:62-70 — fsopen/fsconfig/fsmount/move_mount
    let _ = (workspace_root, handle);
    todo!()
}

/// Non-Linux stub: overlayfs mount syscalls do not exist off Linux.
#[cfg(not(target_os = "linux"))]
pub fn mount_overlay(
    _workspace_root: &std::path::Path,
    _handle: &OverlayHandle,
) -> Result<OverlayMount> {
    Err(OverlayError::Unsupported)
}
