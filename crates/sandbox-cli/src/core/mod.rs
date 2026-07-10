//! Shared client core for the sandbox CLI binaries.
//!
//! Owns the gateway client transport, CLI config discovery, catalog-driven
//! request building, and response/error/help rendering. It is space- and
//! operation-agnostic: it works against any `CliOperationCatalogDocument` and
//! never links a concrete operation catalog or a manager/runtime engine.
#![forbid(unsafe_code)]

pub mod client;
pub mod output;
pub mod request_builder;

pub use sandbox_config::configs::cli::{
    ConfigError, GatewayConfig, GatewayConfigOverrides, DEFAULT_GATEWAY_SOCKET,
    SANDBOX_GATEWAY_SOCKET_ENV,
};
