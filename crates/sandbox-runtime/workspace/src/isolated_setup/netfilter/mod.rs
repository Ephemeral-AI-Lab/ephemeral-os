use std::io::Write;
use std::process::{Command, Stdio};

use crate::profile::IsolatedNetworkError;
use crate::profile::Rfc1918Egress;

use super::{
    BRIDGE_NETWORK, BRIDGE_PREFIX_LEN, GATEWAY_ADDR, IMDS_ADDR, NFT_FILTER_TABLE, NFT_NAT_TABLE,
    RFC1918_NETS,
};

const NFT_BRIDGE_FILTER_TABLE: &str = "eos_iws_bridge_filter";

pub(super) fn install_static_rules(
    rfc1918_egress: Rfc1918Egress,
    bridge_index: u32,
) -> Result<(), IsolatedNetworkError> {
    add_table("inet", NFT_NAT_TABLE)?;
    add_chain(
        "inet",
        NFT_NAT_TABLE,
        "postrouting",
        "type nat hook postrouting priority 100;",
    )?;
    add_rule(
        "inet",
        NFT_NAT_TABLE,
        "postrouting",
        format!("ip saddr {BRIDGE_NETWORK}/{BRIDGE_PREFIX_LEN} oif != {bridge_index} masquerade"),
    )?;

    add_table("inet", NFT_FILTER_TABLE)?;
    add_chain(
        "inet",
        NFT_FILTER_TABLE,
        "forward",
        "type filter hook forward priority 0;",
    )?;
    add_rule(
        "inet",
        NFT_FILTER_TABLE,
        "forward",
        format!("ip daddr {IMDS_ADDR} drop"),
    )?;
    add_rule(
        "inet",
        NFT_FILTER_TABLE,
        "forward",
        peer_isolation_match(""),
    )?;
    install_bridge_peer_isolation_rule()?;
    if rfc1918_egress == Rfc1918Egress::Deny {
        for (network, prefix_len) in RFC1918_NETS {
            add_rule(
                "inet",
                NFT_FILTER_TABLE,
                "forward",
                format!(
                    "ip daddr {network}/{prefix_len} ip daddr != {BRIDGE_NETWORK}/{BRIDGE_PREFIX_LEN} drop"
                ),
            )?;
        }
    }
    Ok(())
}

fn install_bridge_peer_isolation_rule() -> Result<(), IsolatedNetworkError> {
    add_table("bridge", NFT_BRIDGE_FILTER_TABLE)?;
    add_chain(
        "bridge",
        NFT_BRIDGE_FILTER_TABLE,
        "forward",
        "type filter hook forward priority 0;",
    )?;
    add_rule(
        "bridge",
        NFT_BRIDGE_FILTER_TABLE,
        "forward",
        peer_isolation_match("ether type ip "),
    )
}

fn peer_isolation_match(prefix: &str) -> String {
    format!(
        "{prefix}ip saddr {BRIDGE_NETWORK}/{BRIDGE_PREFIX_LEN} ip saddr != {GATEWAY_ADDR} ip daddr {BRIDGE_NETWORK}/{BRIDGE_PREFIX_LEN} ip daddr != {GATEWAY_ADDR} drop"
    )
}

fn add_table(family: &str, table: &str) -> Result<(), IsolatedNetworkError> {
    run_nft(format!("add table {family} {table}"), true)
}

fn add_chain(
    family: &str,
    table: &str,
    chain: &str,
    hook: &str,
) -> Result<(), IsolatedNetworkError> {
    run_nft(
        format!("add chain {family} {table} {chain} {{ {hook} }}"),
        true,
    )
}

fn add_rule(
    family: &str,
    table: &str,
    chain: &str,
    rule: impl std::fmt::Display,
) -> Result<(), IsolatedNetworkError> {
    run_nft(format!("add rule {family} {table} {chain} {rule}"), true)
}

fn run_nft(command: String, ignore_exists: bool) -> Result<(), IsolatedNetworkError> {
    let mut child = Command::new("nft")
        .arg("-f")
        .arg("-")
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|err| nft_error(&command, err))?;
    let command_line = format!("{command}\n");
    child
        .stdin
        .as_mut()
        .ok_or_else(|| nft_error(&command, "stdin unavailable"))?
        .write_all(command_line.as_bytes())
        .map_err(|err| nft_error(&command, err))?;
    drop(child.stdin.take());
    let output = child
        .wait_with_output()
        .map_err(|err| nft_error(&command, err))?;
    if output.status.success() {
        return Ok(());
    }
    let stderr = String::from_utf8_lossy(&output.stderr);
    if ignore_exists && stderr.to_ascii_lowercase().contains("exists") {
        return Ok(());
    }
    Err(nft_error(&command, stderr.trim()))
}

fn nft_error(command: &str, error: impl std::fmt::Display) -> IsolatedNetworkError {
    IsolatedNetworkError::NetworkUnavailable(format!("nft `{command}`: {error}"))
}
