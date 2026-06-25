use std::ffi::OsString;
use std::path::PathBuf;

#[test]
fn cli_config_precedence_is_cli_env_default() {
    let default_config = GatewayConfig::discover_with(GatewayConfigOverrides::default(), |_| None)
        .expect("default config discovers");
    assert_eq!(
        default_config.gateway_socket_path,
        PathBuf::from(DEFAULT_GATEWAY_SOCKET)
    );

    let env_config =
        GatewayConfig::discover_with(GatewayConfigOverrides::default(), |key| match key {
            SANDBOX_GATEWAY_SOCKET_ENV => Some(OsString::from("/env/gateway.sock")),
            SANDBOX_DEFAULT_ID_ENV => Some(OsString::from("env-sbox")),
            _ => None,
        })
        .expect("env config discovers");
    assert_eq!(
        env_config.gateway_socket_path,
        PathBuf::from("/env/gateway.sock")
    );
    assert_eq!(env_config.default_sandbox_id.as_deref(), Some("env-sbox"));

    let cli_config = GatewayConfig::discover_with(
        GatewayConfigOverrides {
            gateway_socket_path: Some(PathBuf::from("/cli/gateway.sock")),
            gateway_auth_token: None,
            default_sandbox_id: Some("cli-sbox".to_owned()),
        },
        |_| None,
    )
    .expect("cli overrides discover");
    assert_eq!(
        cli_config.gateway_socket_path,
        PathBuf::from("/cli/gateway.sock")
    );
    assert_eq!(cli_config.default_sandbox_id.as_deref(), Some("cli-sbox"));
}

#[test]
fn cli_config_rejects_blank_default_sandbox_id() {
    let err = GatewayConfig::discover_with(
        GatewayConfigOverrides {
            gateway_socket_path: None,
            gateway_auth_token: None,
            default_sandbox_id: Some(" ".to_owned()),
        },
        |_| None,
    )
    .expect_err("blank sandbox id is rejected");

    assert_eq!(err.to_string(), "default sandbox id must be non-empty");
}
