use super::parse_network_config;

type TestResult<T = ()> = Result<T, Box<dyn std::error::Error + Send + Sync>>;

#[test]
fn parse_net_ready_with_optional_veth_config() -> TestResult {
    let config = parse_network_config(b"net-ready eos-iws-abcden 10.244.0.2 24 10.244.0.1\n")
        .ok_or_else(|| std::io::Error::other("network config should parse"))?;

    assert_eq!(config.iface, "eos-iws-abcden");
    assert_eq!(config.ns_ip.to_string(), "10.244.0.2");
    assert_eq!(config.prefix_len, 24);
    assert_eq!(config.gateway.to_string(), "10.244.0.1");
    Ok(())
}
