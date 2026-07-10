#[test]
fn config_console_defaults_preserve_shipped_policy() {
    // prd.yml carries no console section, so the section must load to
    // today's exact constants.
    let config = ConsoleConfig::default();
    config.validate().expect("default console config is valid");
    assert_eq!(config.bind_addr, "127.0.0.1:7880");
    assert!((config.rpc_timeout_s - 120.0).abs() < f64::EPSILON);
    assert!((config.health_probe_timeout_s - 2.0).abs() < f64::EPSILON);
    assert!((config.proxy_connect_timeout_s - 10.0).abs() < f64::EPSILON);
    assert!((config.proxy_response_timeout_s - 30.0).abs() < f64::EPSILON);
    assert!((config.endpoint_resolve_timeout_s - 5.0).abs() < f64::EPSILON);
    assert!((config.endpoint_cache_ttl_s - 3.0).abs() < f64::EPSILON);
}

#[test]
fn config_console_section_overrides_deserialize() {
    let config = console_section(
        "console:
  bind_addr: 127.0.0.1:7999
  rpc_timeout_s: 1.5
  endpoint_cache_ttl_s: 0.25
",
    )
    .expect("console overrides deserialize");
    config.validate().expect("console overrides are valid");
    assert_eq!(config.bind_addr, "127.0.0.1:7999");
    assert!((config.rpc_timeout_s - 1.5).abs() < f64::EPSILON);
    assert!((config.endpoint_cache_ttl_s - 0.25).abs() < f64::EPSILON);
    assert!((config.health_probe_timeout_s - 2.0).abs() < f64::EPSILON);
}

#[test]
fn config_console_rejects_unknown_key() {
    let error = console_section("console:\n  bind: 127.0.0.1:1\n")
        .expect_err("unknown console key must be rejected");
    assert!(error.to_string().contains("bind"), "{error}");
}

#[test]
fn config_validation_rejects_console_edge_values() {
    let invalid = [
        ConsoleConfig {
            bind_addr: "no-port".to_owned(),
            ..ConsoleConfig::default()
        },
        ConsoleConfig {
            rpc_timeout_s: 0.0,
            ..ConsoleConfig::default()
        },
        ConsoleConfig {
            health_probe_timeout_s: -1.0,
            ..ConsoleConfig::default()
        },
        ConsoleConfig {
            proxy_connect_timeout_s: 0.0,
            ..ConsoleConfig::default()
        },
        ConsoleConfig {
            proxy_response_timeout_s: 0.0,
            ..ConsoleConfig::default()
        },
        ConsoleConfig {
            endpoint_resolve_timeout_s: 0.0,
            ..ConsoleConfig::default()
        },
        ConsoleConfig {
            endpoint_cache_ttl_s: f64::NAN,
            ..ConsoleConfig::default()
        },
    ];
    let fields = [
        "console.bind_addr",
        "console.rpc_timeout_s",
        "console.health_probe_timeout_s",
        "console.proxy_connect_timeout_s",
        "console.proxy_response_timeout_s",
        "console.endpoint_resolve_timeout_s",
        "console.endpoint_cache_ttl_s",
    ];
    for (config, field) in invalid.iter().zip(fields) {
        assert_invalid(config, field);
    }
}

fn console_section(yaml: &str) -> Result<ConsoleConfig, crate::ConfigError> {
    crate::ConfigDocument::parse(std::path::Path::new("<test>"), yaml)?.section("console")
}

fn assert_invalid(config: &ConsoleConfig, field: &str) {
    let err = config.validate().expect_err("config should be invalid");
    assert!(err.to_string().contains(field), "{err}");
}
