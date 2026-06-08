//! Resource caps and lifecycle config.

/// Persisted-handles schema tag.
pub const PERSISTED_HANDLES_SCHEMA_VERSION: u32 = 1;

/// Per-workspace handle / veth name prefix.
///
/// This single literal is the one source of truth for both the workspace-handle
/// id seed and the veth name prefix; the contract requires they share one
/// constant (06-crate-map §D.2 — duplicate-literal drift risk).
pub const HANDLE_PREFIX: &str = "eos-iws-";

/// cgroup root the per-workspace cgroup is created under.
pub const CGROUP_ROOT: &str = "/sys/fs/cgroup";

const DEFAULT_EOS_WORKSPACE_ROOT: &str = "/testbed";

/// RFC1918 egress policy.
///
/// `allow` (default) leaves private-network egress open; `deny` installs the
/// RFC1918 drop rules.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Rfc1918Egress {
    /// Private-network egress permitted (default).
    Allow,
    /// Private-network egress dropped.
    Deny,
}

/// Resource caps + lifecycle config. The `Default` impl is the byte-for-byte
/// `from_env` result with no env overrides set.
#[derive(Debug, Clone, PartialEq)]
pub struct ResourceCaps {
    /// Whether the isolated-workspace feature is enabled. Default `false`.
    pub enabled: bool,
    /// Idle TTL before GC reaps a workspace. Default `1800.0` s.
    pub ttl_s: f64,
    /// Global concurrent-workspace cap. Default `5`.
    pub total_cap: u32,
    /// Upperdir size cap. Default `1073741824` (1 GiB).
    pub upperdir_bytes: u64,
    /// Fraction of `MemAvailable` admitted per workspace. Default `0.5`.
    pub memavail_fraction: f64,
    /// Per-enter setup timeout. Default `30.0` s.
    pub setup_timeout_s: f64,
    /// Exit drain grace window (clamped `>= 0.0`). Default `0.25` s.
    pub exit_grace_s: f64,
    /// RFC1918 egress policy. Default `Allow`.
    pub rfc1918_egress: Rfc1918Egress,
    /// Fallback DNS resolver written into the namespace. Default `"1.1.1.1"`.
    pub fallback_dns: String,
    /// Visible EOS workspace mount root. Default `"/testbed"`.
    pub eos_workspace_root: String,
    /// Phase-sampler tick interval (clamped `>= 0.01`). Default `0.5` s.
    pub sample_interval_s: f64,
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
            sample_interval_s: 0.5,
        }
    }
}
