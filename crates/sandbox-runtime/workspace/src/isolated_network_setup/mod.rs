use std::collections::BTreeSet;
use std::net::Ipv4Addr;

use crate::session::Rfc1918Egress;
use crate::session::WorkspaceManagerError;

#[cfg(target_os = "linux")]
mod rtnl;

#[cfg(target_os = "linux")]
use rtnl::{ensure_bridge, ignore_not_found, install_veth_pair, link_index, run_netlink};

#[cfg(target_os = "linux")]
pub const BRIDGE_NAME: &str = "eos-shared0";
#[cfg(target_os = "linux")]
pub const GATEWAY: &str = "10.244.0.1";
#[cfg(target_os = "linux")]
pub const GATEWAY_ADDR: Ipv4Addr = Ipv4Addr::new(10, 244, 0, 1);
pub(crate) const VETH_PREFIX: &str = "eos-iws-";

#[cfg(target_os = "linux")]
pub const BRIDGE_PREFIX_LEN: u8 = 24;
pub(crate) const POOL_FIRST_HOST: u8 = 2;
pub(crate) const POOL_LAST_HOST: u8 = 254;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VethAllocation {
    pub host_name: String,
    pub ns_name: String,
    pub ns_ip: Ipv4Addr,
}

#[must_use]
pub(crate) fn veth_names(workspace_handle_id: &str) -> (String, String) {
    let short: String = workspace_handle_id.chars().take(6).collect();
    (
        format!("{VETH_PREFIX}{short}h"),
        format!("{VETH_PREFIX}{short}n"),
    )
}

#[derive(Debug, Clone, Default)]
pub(crate) struct BridgeAddressPool {
    allocated: BTreeSet<Ipv4Addr>,
}

impl BridgeAddressPool {
    pub(crate) fn allocate(&mut self) -> Result<Ipv4Addr, WorkspaceManagerError> {
        for host in POOL_FIRST_HOST..=POOL_LAST_HOST {
            let ip = Ipv4Addr::new(10, 244, 0, host);
            if self.allocated.insert(ip) {
                return Ok(ip);
            }
        }
        Err(WorkspaceManagerError::NetworkUnavailable(
            "isolated_ip_pool_exhausted".to_owned(),
        ))
    }

    pub(crate) fn free(&mut self, ip: Ipv4Addr) {
        self.allocated.remove(&ip);
    }
}

#[derive(Debug)]
pub(crate) struct IsolatedNetwork {
    rfc1918_egress: Rfc1918Egress,
    pool: BridgeAddressPool,
    initialized: bool,
}

impl IsolatedNetwork {
    #[must_use]
    pub(crate) fn new(rfc1918_egress: Rfc1918Egress) -> Self {
        Self {
            rfc1918_egress,
            pool: BridgeAddressPool::default(),
            initialized: false,
        }
    }

    pub(crate) fn initialize(&mut self) -> Result<(), WorkspaceManagerError> {
        self.validate_packet_filter_policy()?;
        #[cfg(target_os = "linux")]
        {
            run_netlink(move |handle| async move {
                ensure_bridge(&handle).await?;
                Ok(())
            })?;
        }
        self.initialized = true;
        Ok(())
    }

    fn validate_packet_filter_policy(&self) -> Result<(), WorkspaceManagerError> {
        if self.rfc1918_egress == Rfc1918Egress::Deny {
            return Err(WorkspaceManagerError::NetworkUnavailable(
                "rfc1918_egress=deny requires packet filtering; no-install isolated networking supports workspace peer isolation only"
                    .to_owned(),
            ));
        }
        Ok(())
    }

    pub(crate) fn install_veth(
        &mut self,
        workspace_handle_id: &str,
        holder_pid: i32,
    ) -> Result<VethAllocation, WorkspaceManagerError> {
        if !self.initialized {
            self.initialize()?;
        }
        let allocation = self.allocate_veth(workspace_handle_id)?;
        if holder_pid > 0 {
            #[cfg(target_os = "linux")]
            {
                let host = allocation.host_name.clone();
                let ns = allocation.ns_name.clone();
                let holder_pid = u32::try_from(holder_pid).map_err(|_| {
                    WorkspaceManagerError::NetworkUnavailable(format!(
                        "invalid isolated holder pid {holder_pid}"
                    ))
                })?;
                if let Err(error) = run_netlink(move |handle| async move {
                    install_veth_pair(&handle, &host, &ns, holder_pid).await
                }) {
                    self.pool.free(allocation.ns_ip);
                    return Err(error);
                }
            }
        }
        Ok(allocation)
    }

    fn allocate_veth(
        &mut self,
        workspace_handle_id: &str,
    ) -> Result<VethAllocation, WorkspaceManagerError> {
        let (host_name, ns_name) = veth_names(workspace_handle_id);
        let ns_ip = self.pool.allocate()?;
        Ok(VethAllocation {
            host_name,
            ns_name,
            ns_ip,
        })
    }

    pub(crate) fn teardown_veth(
        &mut self,
        allocation: &VethAllocation,
    ) -> Result<(), WorkspaceManagerError> {
        #[cfg(target_os = "linux")]
        {
            let host_name = allocation.host_name.clone();
            run_netlink(move |handle| async move {
                if let Some(index) = link_index(&handle, &host_name).await? {
                    ignore_not_found("delete host veth", handle.link().del(index).execute().await)?;
                }
                Ok(())
            })?;
        }
        self.pool.free(allocation.ns_ip);
        Ok(())
    }
}

#[cfg(target_os = "linux")]
pub(crate) fn network_error_at(
    step: impl Into<String>,
    error: impl std::fmt::Display,
) -> WorkspaceManagerError {
    WorkspaceManagerError::NetworkUnavailable(format!("{}: {error}", step.into()))
}
