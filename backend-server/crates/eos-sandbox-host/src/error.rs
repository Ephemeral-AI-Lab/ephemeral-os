//! The single `thiserror` error enum for this crate (spec-conventions §8).

use eos_types::{JsonObject, SandboxId};

/// Every fallible operation in `eos-sandbox-host` returns this one error enum.
///
/// `#[non_exhaustive]` so new daemon/provider failure modes can be added without
/// a breaking change; in-crate matches stay exhaustive. Messages are lowercase
/// with no trailing punctuation (`err-lowercase-msg`).
#[derive(Debug, thiserror::Error)]
#[non_exhaustive]
pub enum SandboxHostError {
    /// `default()` was called before `set_default` seeded the registry.
    #[error("no default sandbox provider registered")]
    NoDefaultProvider,
    /// A typed lookup wanted an adapter for a sandbox with no binding and no
    /// default fallback available.
    #[error("no adapter for sandbox {0}")]
    UnknownSandbox(SandboxId),
    /// A provider `exec` returned a non-zero exit the caller treats as fatal.
    #[error("provider exec failed (exit {exit_code}): {message}")]
    ExecFailed {
        /// The process exit code reported by the provider.
        exit_code: i32,
        /// A human-readable failure message (often the captured stderr tail).
        message: String,
    },
    /// The daemon returned a non-policy `error` envelope for a dispatched op.
    #[error("daemon dispatch failed: {kind}: {message}")]
    DaemonDispatch {
        /// The daemon error kind/class string.
        kind: String,
        /// The daemon error message.
        message: String,
        /// The untyped daemon error payload (verbatim).
        details: JsonObject,
    },
    /// A readiness probe reported the resident daemon is not ready.
    #[error("daemon not ready")]
    DaemonNotReady {
        /// The untyped readiness payload (verbatim) for diagnostics.
        details: JsonObject,
    },
    /// The daemon thin-client produced output that did not decode to a JSON
    /// envelope.
    #[error("bad daemon response")]
    BadResponse {
        /// The raw stdout the thin client emitted.
        stdout: String,
    },
    /// The pinned `eosd` artifact failed its sha256 check before upload.
    #[error("eosd artifact hash mismatch for {arch}: got {got}, expected {expected}")]
    ArtifactHashMismatch {
        /// The container architecture token (`amd64` / `arm64`).
        arch: String,
        /// The hash computed over the on-disk artifact.
        got: String,
        /// The hash pinned in [`crate::bootstrap_artifact`].
        expected: String,
    },
    /// No pinned `eosd` artifact exists for the resolved architecture.
    #[error("eosd artifact missing for {arch}")]
    ArtifactMissing {
        /// The container architecture token (`amd64` / `arm64`).
        arch: String,
    },
    /// `uname -m` reported a machine the host cannot map to an `eosd` arch.
    #[error("unsupported sandbox architecture for eosd artifact: {machine}")]
    UnsupportedArchitecture {
        /// The raw `uname -m` token the host could not map.
        machine: String,
    },
    /// A request carried contradictory or missing required arguments (e.g. a
    /// Docker `create` with neither `image` nor `snapshot`). This is the spec
    /// enum's growth slot (`#[non_exhaustive]`) for argument-validation failures
    /// the §5 list did not enumerate.
    #[error("invalid sandbox request: {0}")]
    InvalidRequest(String),
    /// A `bollard` Docker Engine API call failed.
    #[error("docker error")]
    Docker(#[source] bollard::errors::Error),
    /// A transport / filesystem I/O error.
    #[error("transport io error")]
    Io(#[from] std::io::Error),
    /// A JSON (de)serialization error.
    #[error("json error")]
    Json(#[from] serde_json::Error),
}
