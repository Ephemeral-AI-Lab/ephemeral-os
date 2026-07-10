//! Web console HTTP server for the EphemeralOS sandbox.
//!
//! A client peer of the three `sandbox-cli` binaries, built on
//! `sandbox_cli::core::GatewayClient`. It serves the SPA assets and bridges
//! the browser to the gateway JSON-line protocol (`/api/rpc`, `/api/catalog`)
//! and to each sandbox's `daemon_http` surface (`/api/sandboxes/:id/health`,
//! `/api/sandboxes/:id/files/:op`, `/api/sandboxes/:id/observability/:view`,
//! and the `/s/:id/...` preview proxy). It defines no operation vocabulary,
//! never contacts the daemon RPC endpoint directly, and never exposes the
//! gateway auth token to the browser.
#![forbid(unsafe_code)]

pub mod assets;
pub mod catalog;
pub mod config;
pub mod daemon_api;
pub mod endpoint;
pub mod health;
pub mod proxy;
pub mod response;
pub mod router;
pub mod rpc;
pub mod server;
pub mod state;
