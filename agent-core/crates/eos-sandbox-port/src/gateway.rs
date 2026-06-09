//! The single injected sandbox handle (spec §Core Contracts / Sandbox Gateway).
//!
//! [`SandboxGateway`] is the one production seam request orchestration accepts to wire
//! sandbox access. It hands back the two narrower port objects —
//! [`SandboxTransport`] (per-tool daemon RPC) and [`RequestProvisioner`] (request
//! binding) — which share one registry/lifecycle inside the implementor. The
//! concrete implementor (`SandboxManager`) lives in
//! `backend-server/crates/eos-backend-runtime`; this crate only declares the
//! contract so agent-core composes against the port without importing the host.
//!
//! Object-safe by construction (one `&self` accessor per port returning a trait
//! object), so it is stored as `Arc<dyn SandboxGateway>` at the composition
//! root. The repo targets Rust 1.85, which has no trait-object upcasting, so the
//! gateway exposes `transport()` / `provisioner()` accessors that return the
//! narrower handles explicitly rather than relying on an upcast.

use std::sync::Arc;

use crate::provision::RequestProvisioner;
use crate::transport::SandboxTransport;

/// One injected sandbox handle exposing the transport and provisioner ports.
///
/// A single handle (rather than two independently injected ports) keeps the
/// transport and provisioner anchored to the same backend registry/lifecycle.
pub trait SandboxGateway: Send + Sync + std::fmt::Debug {
    /// The daemon RPC transport used by sandbox tool execution.
    fn transport(&self) -> Arc<dyn SandboxTransport>;

    /// The request-scoped sandbox provisioner used by runtime bootstrap.
    fn provisioner(&self) -> Arc<dyn RequestProvisioner>;
}
