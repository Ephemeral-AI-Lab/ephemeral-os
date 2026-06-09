#![cfg(target_os = "linux")]

mod isolated {
    pub mod error {
        pub use eos_workspace_runtime::isolated::IsolatedError;
    }

    pub mod network {
        pub use eos_workspace_runtime::isolated::network::{BRIDGE_PREFIX_LEN, IMDS_ADDR};
    }
}

#[path = "../../src/isolated/network/netfilter/exprs.rs"]
mod exprs;
#[path = "../../src/isolated/network/netfilter/wire.rs"]
mod wire;

#[test]
fn peer_isolation_rule_builds_drop_verdict() {
    let expressions = exprs::nft_peer_isolation_rule_exprs().expect("peer isolation rule");

    assert!(expressions.len() > 8);
    let verdict = expressions.last().expect("drop verdict expression");
    assert!(String::from_utf8_lossy(verdict).contains("immediate"));
}
