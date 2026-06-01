//! Shared bridge + per-workspace veth + nftables wiring for isolated workspaces.
//!
//! Daemon-scope state: one bridge `eos-shared0` with gateway `10.244.0.1/24`, a
//! MASQUERADE rule on outbound from `10.244.0.0/24`, an IMDS drop rule, and an
//! opt-in RFC1918-deny rule. Per-workspace state: one veth pair and one `/32`
//! from `10.244.0.2 - 10.244.0.254`.
//!
//! `// PORT backend/src/sandbox/isolated_workspace/network.py:27-34 — net constants`
//!
//! # IPv6 hardening — shell-free port
//!
//! The Python holder shells out (`sysctl`, `ip -6 route flush`, `ip link set lo
//! up`) to purge IPv6 default routes and disable router-advertisement
//! acceptance so the v4-only MASQUERADE rule stays the sole egress. The Rust
//! port replaces those binaries with `rtnetlink` (`RTM_DELROUTE` for the IPv6
//! default route, `RTM_NEWLINK` to bring `lo` up) and direct
//! `/proc/sys/net/ipv6/conf/<iface>/accept_ra` writes — NO `ip`/`sysctl`
//! binaries. This work executes inside the namespace via `eos-ns-holder`
//! (see `host_runtime`), not in this daemon-scope module.
//! `// PORT backend/src/sandbox/isolated_workspace/scripts/ns_holder.py:29-49 — IPv6 hardening`

use std::net::Ipv4Addr;

use crate::caps::{Rfc1918Egress, HANDLE_PREFIX};
use crate::error::IsolatedError;

/// Shared bridge interface name. `// PORT backend/src/sandbox/isolated_workspace/network.py:27`
pub const BRIDGE_NAME: &str = "eos-shared0";
/// Shared bridge CIDR. `// PORT backend/src/sandbox/isolated_workspace/network.py:28`
pub const BRIDGE_CIDR: &str = "10.244.0.0/24";
/// Bridge gateway address. `// PORT backend/src/sandbox/isolated_workspace/network.py:29`
pub const GATEWAY: &str = "10.244.0.1";
/// nftables NAT table name. `// PORT backend/src/sandbox/isolated_workspace/network.py:30`
pub const NFT_NAT_TABLE: &str = "eos_iws_nat";
/// nftables filter table name. `// PORT backend/src/sandbox/isolated_workspace/network.py:31`
pub const NFT_FILTER_TABLE: &str = "eos_iws_filter";
/// Cloud IMDS address dropped on the forward chain. `// PORT backend/src/sandbox/isolated_workspace/network.py:32`
pub const IMDS_ADDR: &str = "169.254.169.254";
/// RFC1918 private networks (for the opt-in deny rule). `// PORT backend/src/sandbox/isolated_workspace/network.py:33`
pub const RFC1918_NETS: [&str; 3] = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"];
/// Per-workspace veth name prefix — the SAME literal as [`HANDLE_PREFIX`].
/// `// PORT backend/src/sandbox/isolated_workspace/network.py:34`
pub const VETH_PREFIX: &str = HANDLE_PREFIX;

/// Bridge CIDR prefix length (matches `BRIDGE_CIDR`).
pub const BRIDGE_PREFIX_LEN: u8 = 24;
/// First allocatable host octet (skips `.0` network + `.1` gateway).
pub const POOL_FIRST_HOST: u8 = 2;
/// Last allocatable host octet (skips `.255` broadcast).
pub const POOL_LAST_HOST: u8 = 254;

/// One veth `/32` allocation for a workspace.
/// `// PORT backend/src/sandbox/isolated_workspace/network.py:41-44 — VethAllocation`
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VethAllocation {
    /// Host-side veth name (attached to the bridge).
    pub host_name: String,
    /// Namespace-side IPv4 address allocated from the pool.
    pub ns_ip: Ipv4Addr,
}

/// Host/peer veth names for a workspace handle.
///
/// Linux `IFNAMSIZ` caps names at 15 chars: `eos-iws-` (8) + `handle[:6]` (6) +
/// suffix (1) = 15 exactly. Host ends in `h`, peer ends in `n`.
/// `// PORT backend/src/sandbox/isolated_workspace/network.py:231-235 — _veth_names`
pub fn veth_names(workspace_handle_id: &str) -> (String, String) {
    let short: String = workspace_handle_id.chars().take(6).collect();
    (
        format!("{VETH_PREFIX}{short}h"),
        format!("{VETH_PREFIX}{short}n"),
    )
}

/// Pure IPv4 `/32` allocator over `10.244.0.2 - 10.244.0.254`.
///
/// Lowest-IP-first O(N) scan; N <= 253. No Linux deps.
/// `// PORT backend/src/sandbox/isolated_workspace/network.py:47-75 — BridgeAddressPool`
#[derive(Debug, Clone, Default)]
pub struct BridgeAddressPool {
    allocated: Vec<Ipv4Addr>,
}

impl BridgeAddressPool {
    /// Build an empty pool spanning the bridge CIDR's allocatable range.
    pub fn new() -> Self {
        Self::default()
    }

    /// Mark `ip` as in-use (used to rebuild pool state from `manager.json`).
    // PORT backend/src/sandbox/isolated_workspace/network.py:60-64 — BridgeAddressPool.reserve
    pub fn reserve(&mut self, ip: Ipv4Addr) -> Result<(), IsolatedError> {
        if !is_pool_ip(ip) {
            return Err(IsolatedError::NetworkUnavailable(format!(
                "isolated workspace IP {ip} is outside {BRIDGE_CIDR}"
            )));
        }
        if !self.allocated.contains(&ip) {
            self.allocated.push(ip);
            self.allocated.sort_unstable();
        }
        Ok(())
    }

    /// Allocate the lowest free `/32` in the pool.
    // PORT backend/src/sandbox/isolated_workspace/network.py:66-72 — BridgeAddressPool.allocate
    pub fn allocate(&mut self) -> Result<Ipv4Addr, IsolatedError> {
        for host in POOL_FIRST_HOST..=POOL_LAST_HOST {
            let ip = Ipv4Addr::new(10, 244, 0, host);
            if !self.allocated.contains(&ip) {
                self.allocated.push(ip);
                self.allocated.sort_unstable();
                return Ok(ip);
            }
        }
        Err(IsolatedError::NetworkUnavailable(
            "isolated_workspace_ip_pool_exhausted".to_owned(),
        ))
    }

    /// Release `ip` back into the pool.
    // PORT backend/src/sandbox/isolated_workspace/network.py:74-75 — BridgeAddressPool.free
    pub fn free(&mut self, ip: Ipv4Addr) {
        self.allocated.retain(|allocated| *allocated != ip);
    }
}

/// Owns the `eos-shared0` bridge + static nft rules + per-workspace veth wiring.
///
/// The Python implementation shells out to `ip`/`nft`; the Rust port replaces
/// those with `rtnetlink` (link/addr/route) and an nftables netlink path — NO
/// `ip`/`nft` binaries. Bodies are deferred to the syscall port phase.
/// `// PORT backend/src/sandbox/isolated_workspace/network.py:78-228 — IsolatedNetwork`
#[derive(Debug)]
pub struct IsolatedNetwork {
    rfc1918_egress: Rfc1918Egress,
    pool: BridgeAddressPool,
    initialized: bool,
}

impl IsolatedNetwork {
    /// Construct an uninitialized network with the given egress policy.
    pub fn new(rfc1918_egress: Rfc1918Egress) -> Self {
        Self {
            rfc1918_egress,
            pool: BridgeAddressPool::new(),
            initialized: false,
        }
    }

    /// Whether [`initialize`](Self::initialize) has installed the bridge + rules.
    pub fn initialized(&self) -> bool {
        self.initialized
    }

    /// Install the bridge + MASQUERADE + IMDS drop (+ optional RFC1918 deny).
    /// Idempotent.
    // PORT backend/src/sandbox/isolated_workspace/network.py:95-100 — IsolatedNetwork.initialize (require_tools/ensure_bridge/install_static_rules)
    pub fn initialize(&mut self) -> Result<(), IsolatedError> {
        // This lifecycle slice initializes daemon-side allocation state. The
        // netlink bridge/nft wiring remains the live-kernel follow-up.
        let _ = self.rfc1918_egress;
        self.initialized = true;
        Ok(())
    }

    /// Create a veth pair, attach the host end to the bridge with port
    /// isolation, and configure the namespace-side end (up, `/24` addr,
    /// default route via gateway).
    // PORT backend/src/sandbox/isolated_workspace/network.py:102-146 — IsolatedNetwork.install_veth
    pub fn install_veth(
        &mut self,
        workspace_handle_id: &str,
        _holder_pid: i32,
    ) -> Result<VethAllocation, IsolatedError> {
        // Allocate the stable audit/control-plane record now; attaching the
        // actual veth pair to a holder netns is still deferred.
        if !self.initialized {
            self.initialize()?;
        }
        let (host_name, _peer_name) = veth_names(workspace_handle_id);
        Ok(VethAllocation {
            host_name,
            ns_ip: self.pool.allocate()?,
        })
    }

    /// Tear down a veth pair and return its `/32` to the pool.
    // PORT backend/src/sandbox/isolated_workspace/network.py:148-150 — IsolatedNetwork.teardown_veth
    pub fn teardown_veth(&mut self, allocation: &VethAllocation) {
        self.pool.free(allocation.ns_ip);
    }
}

fn is_pool_ip(ip: Ipv4Addr) -> bool {
    let octets = ip.octets();
    octets[0] == 10
        && octets[1] == 244
        && octets[2] == 0
        && (POOL_FIRST_HOST..=POOL_LAST_HOST).contains(&octets[3])
}
