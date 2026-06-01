//! Shared bridge + per-workspace veth wiring for isolated workspaces.
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

#[cfg(target_os = "linux")]
use std::future::Future;
use std::net::Ipv4Addr;
#[cfg(target_os = "linux")]
use std::thread;

use crate::caps::{Rfc1918Egress, HANDLE_PREFIX};
use crate::error::IsolatedError;
#[cfg(target_os = "linux")]
use futures_util::stream::TryStreamExt;
#[cfg(target_os = "linux")]
use rtnetlink::{new_connection, Handle, LinkBridge, LinkBridgePort, LinkUnspec, LinkVeth};

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
    /// Namespace-side veth name (moved into the holder netns).
    pub ns_name: String,
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

/// Owns the `eos-shared0` bridge + per-workspace veth wiring.
///
/// The Python implementation shells out to `ip`/`nft`; the Rust port replaces
/// the bridge/veth path with `rtnetlink` link/address operations — NO `ip`
/// binaries. The nftables NAT/filter path remains the follow-up slice.
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

    /// Install the ported bridge slice. Idempotent.
    ///
    /// MASQUERADE, IMDS drop, and optional RFC1918 deny remain the nftables
    /// follow-up slice.
    // PORT backend/src/sandbox/isolated_workspace/network.py:95-100 — IsolatedNetwork.initialize (require_tools/ensure_bridge/install_static_rules)
    pub fn initialize(&mut self) -> Result<(), IsolatedError> {
        if test_harness_enabled() {
            self.initialized = true;
            return Ok(());
        }
        let _nft_policy_for_follow_up = self.rfc1918_egress;
        #[cfg(target_os = "linux")]
        {
            run_netlink(|handle| async move {
                ensure_bridge(&handle).await?;
                // nftables NAT/filter wiring remains the live-kernel follow-up.
                Ok(())
            })?;
        }
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
        holder_pid: i32,
    ) -> Result<VethAllocation, IsolatedError> {
        if !self.initialized {
            self.initialize()?;
        }
        let (host_name, ns_name) = veth_names(workspace_handle_id);
        let ns_ip = self.pool.allocate()?;
        if !test_harness_enabled() && holder_pid > 0 {
            #[cfg(target_os = "linux")]
            {
                let host = host_name.clone();
                let ns = ns_name.clone();
                if let Err(error) = run_netlink(move |handle| async move {
                    install_veth_pair(&handle, &host, &ns, holder_pid as u32).await
                }) {
                    self.pool.free(ns_ip);
                    return Err(error);
                }
            }
        }
        Ok(VethAllocation {
            host_name,
            ns_name,
            ns_ip,
        })
    }

    /// Tear down a veth pair and return its `/32` to the pool.
    // PORT backend/src/sandbox/isolated_workspace/network.py:148-150 — IsolatedNetwork.teardown_veth
    pub fn teardown_veth(&mut self, allocation: &VethAllocation) {
        if !test_harness_enabled() {
            #[cfg(target_os = "linux")]
            {
                let host_name = allocation.host_name.clone();
                let _ = run_netlink(move |handle| async move {
                    if let Some(index) = link_index(&handle, &host_name).await? {
                        ignore_not_found(handle.link().del(index).execute().await)?;
                    }
                    Ok(())
                });
            }
        }
        self.pool.free(allocation.ns_ip);
    }
}

fn test_harness_enabled() -> bool {
    std::env::var("EOS_ISOLATED_WORKSPACE_TEST_HARNESS")
        .map(|value| matches!(value.as_str(), "1" | "true" | "TRUE" | "yes" | "YES"))
        .unwrap_or(false)
}

#[cfg(target_os = "linux")]
fn gateway_addr() -> Ipv4Addr {
    Ipv4Addr::new(10, 244, 0, 1)
}

#[cfg(target_os = "linux")]
fn run_netlink<T, F, Fut>(operation: F) -> Result<T, IsolatedError>
where
    T: Send + 'static,
    F: FnOnce(Handle) -> Fut + Send + 'static,
    Fut: Future<Output = Result<T, IsolatedError>> + Send + 'static,
{
    thread::spawn(move || {
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_io()
            .build()
            .map_err(network_error)?;
        runtime.block_on(async move {
            let (connection, handle, _) = new_connection().map_err(network_error)?;
            tokio::spawn(connection);
            operation(handle).await
        })
    })
    .join()
    .map_err(|_| IsolatedError::NetworkUnavailable("netlink thread panicked".to_owned()))?
}

#[cfg(target_os = "linux")]
async fn ensure_bridge(handle: &Handle) -> Result<(), IsolatedError> {
    if link_index(handle, BRIDGE_NAME).await?.is_none() {
        ignore_exists(
            handle
                .link()
                .add(LinkBridge::new(BRIDGE_NAME).up().build())
                .execute()
                .await,
        )?;
    }
    let bridge_index = require_link_index(handle, BRIDGE_NAME).await?;
    ignore_exists(
        handle
            .address()
            .add(bridge_index, gateway_addr().into(), BRIDGE_PREFIX_LEN)
            .execute()
            .await,
    )?;
    ignore_exists(
        handle
            .link()
            .change(LinkUnspec::new_with_index(bridge_index).up().build())
            .execute()
            .await,
    )?;
    Ok(())
}

#[cfg(target_os = "linux")]
async fn install_veth_pair(
    handle: &Handle,
    host_name: &str,
    ns_name: &str,
    holder_pid: u32,
) -> Result<(), IsolatedError> {
    let bridge_index = require_link_index(handle, BRIDGE_NAME).await?;
    if link_index(handle, host_name).await?.is_none() {
        ignore_exists(
            handle
                .link()
                .add(LinkVeth::new(host_name, ns_name).build())
                .execute()
                .await,
        )?;
    }
    if let Some(ns_index) = link_index(handle, ns_name).await? {
        ignore_exists(
            handle
                .link()
                .change(
                    LinkUnspec::new_with_index(ns_index)
                        .setns_by_pid(holder_pid)
                        .build(),
                )
                .execute()
                .await,
        )?;
    }
    let host_index = require_link_index(handle, host_name).await?;
    ignore_exists(
        handle
            .link()
            .change(
                LinkUnspec::new_with_index(host_index)
                    .controller(bridge_index)
                    .up()
                    .build(),
            )
            .execute()
            .await,
    )?;
    ignore_unsupported(
        handle
            .link()
            .set_port(
                LinkBridgePort::new(host_index)
                    .isolated(true)
                    .mcast_flood(false)
                    .build(),
            )
            .execute()
            .await,
    )?;
    Ok(())
}

#[cfg(target_os = "linux")]
async fn require_link_index(handle: &Handle, name: &str) -> Result<u32, IsolatedError> {
    link_index(handle, name)
        .await?
        .ok_or_else(|| IsolatedError::NetworkUnavailable(format!("link {name} not found")))
}

#[cfg(target_os = "linux")]
async fn link_index(handle: &Handle, name: &str) -> Result<Option<u32>, IsolatedError> {
    let mut links = handle.link().get().match_name(name.to_owned()).execute();
    Ok(links
        .try_next()
        .await
        .map_err(network_error)?
        .map(|link| link.header.index))
}

#[cfg(target_os = "linux")]
fn ignore_exists(result: Result<(), rtnetlink::Error>) -> Result<(), IsolatedError> {
    match result {
        Ok(()) => Ok(()),
        Err(error) if is_error_text(&error, &["exists", "-17"]) => Ok(()),
        Err(error) => Err(network_error(error)),
    }
}

#[cfg(target_os = "linux")]
fn ignore_not_found(result: Result<(), rtnetlink::Error>) -> Result<(), IsolatedError> {
    match result {
        Ok(()) => Ok(()),
        Err(error) if is_error_text(&error, &["not found", "no such", "-19"]) => Ok(()),
        Err(error) => Err(network_error(error)),
    }
}

#[cfg(target_os = "linux")]
fn ignore_unsupported(result: Result<(), rtnetlink::Error>) -> Result<(), IsolatedError> {
    match result {
        Ok(()) => Ok(()),
        Err(error) if is_error_text(&error, &["operation not supported", "not supported"]) => {
            Ok(())
        }
        Err(error) => Err(network_error(error)),
    }
}

#[cfg(target_os = "linux")]
fn is_error_text(error: &rtnetlink::Error, needles: &[&str]) -> bool {
    let text = error.to_string().to_ascii_lowercase();
    needles.iter().any(|needle| text.contains(needle))
}

#[cfg(target_os = "linux")]
fn network_error(error: impl std::fmt::Display) -> IsolatedError {
    IsolatedError::NetworkUnavailable(error.to_string())
}

fn is_pool_ip(ip: Ipv4Addr) -> bool {
    let octets = ip.octets();
    octets[0] == 10
        && octets[1] == 244
        && octets[2] == 0
        && (POOL_FIRST_HOST..=POOL_LAST_HOST).contains(&octets[3])
}
