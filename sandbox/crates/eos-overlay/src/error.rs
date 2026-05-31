//! Crate error type.

use std::io;

use eos_protocol::CasError;
use thiserror::Error;

/// Failures raised by the overlay kernel-mount and upper-dir capture paths.
#[derive(Debug, Error)]
#[non_exhaustive]
pub enum OverlayError {
    /// The canonical writable root (`/eos-mount-scratch/eos-sandbox-runtime`)
    /// is missing and could not be created. There is intentionally no fallback.
    /// `// PORT backend/src/sandbox/overlay/writable_dirs.py:16-17 — OverlayWritableRootUnavailable`
    #[error("overlay writable root is missing: {0}")]
    WritableRootUnavailable(String),

    /// A path failed `O_DIRECTORY|O_NOFOLLOW` validation (symlink, missing, or
    /// not a directory) before being handed to the mount syscalls.
    /// `// PORT backend/src/sandbox/overlay/kernel_mount.py:158-176`
    #[error("invalid mount input: {0}")]
    InvalidMountInput(String),

    /// A raw mount syscall (`fsopen`/`fsconfig`/`fsmount`/`move_mount`) or an
    /// `umount` failed. `// PORT backend/src/sandbox/overlay/kernel_mount.py:62-70,97-121`
    #[error("overlay mount syscall failed: {0}")]
    MountSyscall(#[source] io::Error),

    /// An upper-dir walk / capture I/O error.
    /// `// PORT backend/src/sandbox/overlay/capture.py:49-89 — _walk_upperdir`
    #[error("upperdir capture failed: {0}")]
    Capture(#[source] io::Error),

    /// A captured overlay path did not normalize to a valid relative layer path.
    #[error(transparent)]
    Path(#[from] CasError),

    /// A captured overlay path change violated the per-kind field contract.
    #[error("invalid overlay path change: {0}")]
    InvalidPathChange(String),

    /// The current target OS provides no overlayfs mount syscalls. Returned by
    /// the `#[cfg(not(target_os = "linux"))]` arms so non-Linux `cargo check`
    /// stays green while the real syscall path is Linux-only.
    #[error("overlay mounts are only supported on linux")]
    Unsupported,
}

/// Crate result alias.
pub type Result<T> = std::result::Result<T, OverlayError>;
