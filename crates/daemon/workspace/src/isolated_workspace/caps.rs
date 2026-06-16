pub(crate) const PERSISTED_HANDLES_SCHEMA_VERSION: u32 = 1;

pub const HANDLE_PREFIX: &str = "eos-iws-";

pub const CGROUP_ROOT: &str = "/sys/fs/cgroup";

const DEFAULT_EOS_WORKSPACE_ROOT: &str = "/testbed";

#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Rfc1918Egress {
    Allow,
    Deny,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ResourceCaps {
    pub enabled: bool,
    pub ttl_s: f64,
    pub total_cap: u32,
    pub upperdir_bytes: u64,
    pub memavail_fraction: f64,
    pub setup_timeout_s: f64,
    pub exit_grace_s: f64,
    pub rfc1918_egress: Rfc1918Egress,
    pub fallback_dns: String,
    pub eos_workspace_root: String,
}

impl Default for ResourceCaps {
    fn default() -> Self {
        Self {
            enabled: false,
            ttl_s: 1800.0,
            total_cap: 5,
            upperdir_bytes: 1_073_741_824,
            memavail_fraction: 0.5,
            setup_timeout_s: 30.0,
            exit_grace_s: 0.25,
            rfc1918_egress: Rfc1918Egress::Allow,
            fallback_dns: "1.1.1.1".to_owned(),
            eos_workspace_root: DEFAULT_EOS_WORKSPACE_ROOT.to_owned(),
        }
    }
}
