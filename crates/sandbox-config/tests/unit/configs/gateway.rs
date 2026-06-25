use std::path::PathBuf;

#[test]
fn gateway_server_config_constructs_from_defaults() {
    let config = GatewayConfig::new(
        DEFAULT_GATEWAY_SOCKET,
        DEFAULT_GATEWAY_PID,
        DEFAULT_MAX_CONCURRENT_CONNECTIONS,
        None,
    );

    assert_eq!(config.bind_addr, DEFAULT_GATEWAY_SOCKET);
    assert_eq!(config.pid_path, PathBuf::from(DEFAULT_GATEWAY_PID));
    assert_eq!(
        config.max_concurrent_connections,
        DEFAULT_MAX_CONCURRENT_CONNECTIONS
    );
    assert!(config.auth_token.is_none());
}
