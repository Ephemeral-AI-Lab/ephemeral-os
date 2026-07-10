use std::path::PathBuf;
use std::time::Duration;

use sandbox_cli::core::GatewayConfig;
use sandbox_config::configs::console::ConsoleConfig as ConsoleSection;
use sandbox_console::config::{ConsoleConfig, ConsoleConfigOverrides};

fn stub_gateway() -> GatewayConfig {
    GatewayConfig {
        gateway_socket_path: PathBuf::from("127.0.0.1:7878"),
        gateway_auth_token: Some("token".to_owned()),
    }
}

fn yaml_section() -> ConsoleSection {
    ConsoleSection {
        bind_addr: "127.0.0.1:7991".to_owned(),
        rpc_timeout_s: 9.0,
        health_probe_timeout_s: 0.5,
        proxy_connect_timeout_s: 1.5,
        proxy_response_timeout_s: 2.5,
        endpoint_resolve_timeout_s: 3.5,
        endpoint_cache_ttl_s: 0.25,
    }
}

#[test]
fn resolve_defaults_when_no_flag_env_or_yaml() {
    let config = ConsoleConfig::from_sources(
        ConsoleConfigOverrides::default(),
        None,
        None,
        ConsoleSection::default(),
        stub_gateway(),
    );

    assert_eq!(config.bind, "127.0.0.1:7880");
    assert_eq!(config.rpc_timeout, Duration::from_secs(120));
    assert_eq!(config.health_probe_timeout, Duration::from_secs(2));
    assert_eq!(config.proxy_connect_timeout, Duration::from_secs(10));
    assert_eq!(config.proxy_response_timeout, Duration::from_secs(30));
    assert_eq!(config.endpoint_resolve_timeout, Duration::from_secs(5));
    assert_eq!(config.endpoint_cache_ttl, Duration::from_secs(3));
}

#[test]
fn resolve_yaml_section_beats_defaults() {
    let config = ConsoleConfig::from_sources(
        ConsoleConfigOverrides::default(),
        None,
        None,
        yaml_section(),
        stub_gateway(),
    );

    assert_eq!(config.bind, "127.0.0.1:7991");
    assert_eq!(config.rpc_timeout, Duration::from_secs(9));
    assert_eq!(config.health_probe_timeout, Duration::from_millis(500));
    assert_eq!(config.proxy_connect_timeout, Duration::from_millis(1500));
    assert_eq!(config.proxy_response_timeout, Duration::from_millis(2500));
    assert_eq!(config.endpoint_resolve_timeout, Duration::from_millis(3500));
    assert_eq!(config.endpoint_cache_ttl, Duration::from_millis(250));
}

#[test]
fn resolve_flag_beats_env_and_yaml() {
    let overrides = ConsoleConfigOverrides {
        bind: Some("127.0.0.1:7995".to_owned()),
        rpc_timeout_ms: Some(1234),
        ..ConsoleConfigOverrides::default()
    };
    let config = ConsoleConfig::from_sources(
        overrides,
        Some("127.0.0.1:7993".to_owned()),
        None,
        yaml_section(),
        stub_gateway(),
    );

    assert_eq!(config.bind, "127.0.0.1:7995");
    assert_eq!(config.rpc_timeout, Duration::from_millis(1234));
}

#[test]
fn resolve_env_bind_beats_yaml() {
    let config = ConsoleConfig::from_sources(
        ConsoleConfigOverrides::default(),
        Some("127.0.0.1:7993".to_owned()),
        None,
        yaml_section(),
        stub_gateway(),
    );

    assert_eq!(config.bind, "127.0.0.1:7993");
    // Env overrides only the bind; the YAML timeouts still apply.
    assert_eq!(config.rpc_timeout, Duration::from_secs(9));
}

#[test]
fn resolve_assets_flag_beats_env() {
    let config = ConsoleConfig::from_sources(
        ConsoleConfigOverrides {
            assets_dir: Some(PathBuf::from("/tmp/flag-assets")),
            ..ConsoleConfigOverrides::default()
        },
        None,
        Some(PathBuf::from("/tmp/env-assets")),
        ConsoleSection::default(),
        stub_gateway(),
    );
    assert_eq!(config.assets_dir, Some(PathBuf::from("/tmp/flag-assets")));

    let env_only = ConsoleConfig::from_sources(
        ConsoleConfigOverrides::default(),
        None,
        Some(PathBuf::from("/tmp/env-assets")),
        ConsoleSection::default(),
        stub_gateway(),
    );
    assert_eq!(env_only.assets_dir, Some(PathBuf::from("/tmp/env-assets")));
}
