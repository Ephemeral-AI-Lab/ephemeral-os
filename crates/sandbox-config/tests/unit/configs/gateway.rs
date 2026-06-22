use std::path::PathBuf;

#[test]
fn gateway_server_config_constructs_from_defaults() {
    let config = GatewayConfig::new(
        DEFAULT_GATEWAY_SOCKET,
        DEFAULT_GATEWAY_PID,
        DEFAULT_MAX_CONCURRENT_CONNECTIONS,
    );

    assert_eq!(config.socket_path, PathBuf::from(DEFAULT_GATEWAY_SOCKET));
    assert_eq!(config.pid_path, PathBuf::from(DEFAULT_GATEWAY_PID));
    assert_eq!(
        config.max_concurrent_connections,
        DEFAULT_MAX_CONCURRENT_CONNECTIONS
    );
}
