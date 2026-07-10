use std::path::PathBuf;

use sandbox_gateway::{
    resolve_gateway_config, GatewayCliOverrides, GatewayConfig, DEFAULT_GATEWAY_PID,
    DEFAULT_GATEWAY_SOCKET, DEFAULT_MAX_CONCURRENT_CONNECTIONS,
};

fn yaml_section() -> GatewayConfig {
    GatewayConfig::new("127.0.0.1:7911", "/tmp/yaml-gateway.pid", 32, None)
}

#[test]
fn resolve_defaults_when_no_flag_env_or_yaml() {
    let config = resolve_gateway_config(
        GatewayCliOverrides::default(),
        None,
        GatewayConfig::default(),
    );

    assert_eq!(config.bind_addr, DEFAULT_GATEWAY_SOCKET);
    assert_eq!(config.pid_path, PathBuf::from(DEFAULT_GATEWAY_PID));
    assert_eq!(
        config.max_concurrent_connections,
        DEFAULT_MAX_CONCURRENT_CONNECTIONS
    );
}

#[test]
fn resolve_yaml_beats_default() {
    let config = resolve_gateway_config(GatewayCliOverrides::default(), None, yaml_section());

    assert_eq!(config.bind_addr, "127.0.0.1:7911");
    assert_eq!(config.pid_path, PathBuf::from("/tmp/yaml-gateway.pid"));
    assert_eq!(config.max_concurrent_connections, 32);
}

#[test]
fn resolve_flag_beats_yaml() {
    let overrides = GatewayCliOverrides {
        bind_addr: Some("127.0.0.1:7955".to_owned()),
        pid_path: Some(PathBuf::from("/tmp/flag-gateway.pid")),
        max_concurrent_connections: Some(4),
    };
    let config = resolve_gateway_config(overrides, None, yaml_section());

    assert_eq!(config.bind_addr, "127.0.0.1:7955");
    assert_eq!(config.pid_path, PathBuf::from("/tmp/flag-gateway.pid"));
    assert_eq!(config.max_concurrent_connections, 4);
}

#[test]
fn resolve_env_beats_yaml_but_loses_to_flag() {
    let env_only = resolve_gateway_config(
        GatewayCliOverrides::default(),
        Some("127.0.0.1:7922".to_owned()),
        yaml_section(),
    );
    assert_eq!(env_only.bind_addr, "127.0.0.1:7922");
    // Env overrides only the socket; the YAML section keeps the rest.
    assert_eq!(env_only.max_concurrent_connections, 32);

    let flag_and_env = resolve_gateway_config(
        GatewayCliOverrides {
            bind_addr: Some("127.0.0.1:7955".to_owned()),
            ..GatewayCliOverrides::default()
        },
        Some("127.0.0.1:7922".to_owned()),
        yaml_section(),
    );
    assert_eq!(flag_and_env.bind_addr, "127.0.0.1:7955");
}

#[test]
fn resolve_ignores_blank_flag_and_env_sockets() {
    let config = resolve_gateway_config(
        GatewayCliOverrides {
            bind_addr: Some("  ".to_owned()),
            ..GatewayCliOverrides::default()
        },
        Some(String::new()),
        yaml_section(),
    );
    assert_eq!(config.bind_addr, "127.0.0.1:7911");
}

#[test]
fn resolve_keeps_yaml_auth_token_slot_empty() {
    // The auth token never rides YAML; resolution leaves it for the caller.
    let config = resolve_gateway_config(GatewayCliOverrides::default(), None, yaml_section());
    assert!(config.auth_token.is_none());
}
