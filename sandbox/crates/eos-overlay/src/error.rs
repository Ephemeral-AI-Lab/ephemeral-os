//! Crate error type.

use std::io;

use eos_protocol::CasError;
use thiserror::Error;

/// Failures raised by the overlay kernel-mount and upper-dir capture paths.
#[derive(Debug, Error)]
#[non_exhaustive]
pub enum OverlayError {
    /// The canonical writable root (`/eos/mount`)
    /// is missing and could not be created. There is intentionally no fallback.
    #[error("overlay writable root is missing: {0}")]
    WritableRootUnavailable(String),

    /// A path failed `O_DIRECTORY|O_NOFOLLOW` validation (symlink, missing, or
    /// not a directory) before being handed to the mount syscalls.
    #[error("invalid mount input: {0}")]
    InvalidMountInput(String),

    /// A raw mount syscall (`fsopen`/`fsconfig`/`fsmount`/`move_mount`) or an
    /// `umount` failed.
    #[error("overlay mount syscall failed at {context}: {source}")]
    MountSyscall {
        context: &'static str,
        #[source]
        source: io::Error,
    },

    /// An upper-dir walk / capture I/O error.
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
