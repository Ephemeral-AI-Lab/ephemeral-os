//! eos-sandbox-host — the host side of the sandbox.
//!
//! Uses Docker as the only Rust production sandbox provider, owns the per-process
//! [`ProviderRegistry`] as explicit application state, runs container lifecycle
//! with post-lifecycle setup, transports JSON envelopes to the resident
//! in-sandbox daemon with spawn/connect recovery and typed error decoding, and
//! uploads + verifies the pinned `eosd` runtime artifact. See
//! `docs/plans/backend_agent_core_rust_migration/impl-eos-sandbox-host.md`.
#![forbid(unsafe_code)]
#![warn(missing_docs)]

mod daemon_client;
mod docker;
mod error;
mod provider;
mod registry;
mod runtime_artifact;

#[cfg(test)]
mod testutil;

pub use daemon_client::{
    with_daemon_protocol_version, DaemonClient, DAEMON_PROTOCOL_VERSION, DEFAULT_LAYER_STACK_ROOT,
};
pub use docker::DockerProviderAdapter;
pub use error::SandboxHostError;
pub use provider::{
    ContextPreparer, CreateSandboxSpec, DaemonTcpEndpoint, DockerContextPreparer, ExecOpts, Labels,
    PreviewUrl, ProviderAdapter, ProviderHealth, ProviderKind, RawExecResult, SandboxInfo,
    SnapshotInfo,
};
pub use registry::{resolve_provider_kind, ProviderRegistry};
pub use runtime_artifact::{EOSD_VERSION, MINISIGN_PUBLIC_KEY, PROTOCOL_VERSION};
