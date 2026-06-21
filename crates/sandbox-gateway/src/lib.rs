#![forbid(unsafe_code)]

pub mod config;
pub mod connection;
pub mod error;
pub mod lifecycle;
pub mod server;

pub use config::{
    GatewayConfig, DEFAULT_GATEWAY_PID, DEFAULT_GATEWAY_SOCKET, DEFAULT_MAX_CONCURRENT_CONNECTIONS,
    SANDBOX_GATEWAY_SOCKET_ENV,
};
pub use error::GatewayError;
pub use server::SandboxGatewayServer;
