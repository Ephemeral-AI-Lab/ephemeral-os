//! Web console HTTP server for the EphemeralOS sandbox.
//!
//! A client peer of `sandbox-manager-cli` and `sandbox-runtime-cli`, built on
//! `sandbox-cli-core`'s `GatewayClient`. It serves the SPA assets and bridges
//! the browser to the gateway JSON-line protocol (`/api/rpc`, `/api/catalog`)
//! and to each sandbox's `daemon_http` surface (`/api/sandboxes/:id/health`,
//! the `/s/:id/...` preview proxy). It defines no operation vocabulary, never
//! contacts the daemon RPC endpoint directly, and never exposes the gateway
//! auth token to the browser.
#![forbid(unsafe_code)]

pub mod assets;
pub mod catalog;
pub mod config;
pub mod endpoint;
pub mod health;
pub mod proxy;
pub mod response;
pub mod router;
pub mod rpc;
pub mod server;
pub mod state;
