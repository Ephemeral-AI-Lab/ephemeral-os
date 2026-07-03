#![forbid(unsafe_code)]

pub mod gateway;

pub use gateway::{
    GatewayConfig, GatewayError, SandboxGatewayServer, DEFAULT_GATEWAY_PID, DEFAULT_GATEWAY_SOCKET,
    DEFAULT_MAX_CONCURRENT_CONNECTIONS, SANDBOX_GATEWAY_AUTH_TOKEN_ENV, SANDBOX_GATEWAY_SOCKET_ENV,
};
