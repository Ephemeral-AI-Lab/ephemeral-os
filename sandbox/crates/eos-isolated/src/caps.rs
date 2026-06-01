//! Resource caps and lifecycle config, sourced from the environment.
//!
//! Reproduces the `_PipelineConfig.from_env()` defaults byte-for-byte.
//! `// PORT backend/src/sandbox/isolated_workspace/_control_plane/types.py:144-185 — _PipelineConfig`

use std::env;

/// Persisted-handles schema tag. `// PORT backend/src/sandbox/isolated_workspace/_control_plane/types.py:18`
pub const PERSISTED_HANDLES_SCHEMA_VERSION: u32 = 1;

/// Per-workspace handle / veth name prefix.
///
/// This single literal is shared by both `HANDLE_PREFIX` (`types.py:19`) and
/// `VETH_PREFIX` (`network.py:34`); the contract requires one source of truth
/// (06-crate-map §D.2 — duplicate-literal drift risk).
/// `// PORT backend/src/sandbox/isolated_workspace/_control_plane/types.py:19, network.py:34`
pub const HANDLE_PREFIX: &str = "eos-iws-";

/// cgroup root the per-workspace cgroup is created under. `// PORT backend/src/sandbox/isolated_workspace/_control_plane/types.py:20`
pub const CGROUP_ROOT: &str = "/sys/fs/cgroup";

/// Mount target inside the isolated namespace. `// PORT backend/src/sandbox/isolated_workspace/_control_plane/types.py:21`
pub const ISOLATED_WORKSPACE_ROOT: &str = "/testbed";

/// RFC1918 egress policy. `allow` (default) leaves private-network egress open;
/// `deny` installs the RFC1918 drop rules. `// PORT backend/src/sandbox/isolated_workspace/_control_plane/types.py:153,177-179`
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
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
            sample_interval_s: 0.5,
        }
    }
}

impl ResourceCaps {
    /// Build the caps from the process environment, reproducing
    /// `_PipelineConfig.from_env()` parsing + clamping exactly.
    // PORT backend/src/sandbox/isolated_workspace/_control_plane/types.py:161-185 — _PipelineConfig.from_env
    pub fn from_env() -> Self {
        let mut caps = Self::default();
        caps.enabled = env_bool("EOS_ISOLATED_WORKSPACE_ENABLED", false);
        caps.ttl_s = env_f64("EOS_ISOLATED_WORKSPACE_TTL_S", caps.ttl_s);
        caps.total_cap = env_u32("EOS_ISOLATED_WORKSPACE_TOTAL_CAP", caps.total_cap);
        caps.upperdir_bytes = env_u64("EOS_ISOLATED_WORKSPACE_UPPERDIR_BYTES", caps.upperdir_bytes);
        caps.memavail_fraction = env_f64(
            "EOS_ISOLATED_WORKSPACE_MEMAVAIL_FRACTION",
            caps.memavail_fraction,
        );
        caps.setup_timeout_s = env_f64(
            "EOS_ISOLATED_WORKSPACE_SETUP_TIMEOUT_S",
            caps.setup_timeout_s,
        );
        caps.exit_grace_s =
            env_f64("EOS_ISOLATED_WORKSPACE_EXIT_GRACE_S", caps.exit_grace_s).max(0.0);
        caps.rfc1918_egress =
            if env_string("EOS_ISOLATED_WORKSPACE_RFC1918_EGRESS").eq_ignore_ascii_case("deny") {
                Rfc1918Egress::Deny
            } else {
                Rfc1918Egress::Allow
            };
        let fallback_dns = env_string("EOS_ISOLATED_WORKSPACE_FALLBACK_DNS");
        if !fallback_dns.is_empty() {
            caps.fallback_dns = fallback_dns;
        }
        caps.sample_interval_s = env_f64(
            "EOS_ISOLATED_WORKSPACE_SAMPLE_INTERVAL_S",
            caps.sample_interval_s,
        )
        .max(0.01);
        caps
    }
}

fn env_string(key: &str) -> String {
    env::var(key).unwrap_or_default().trim().to_owned()
}

fn env_bool(key: &str, default: bool) -> bool {
    let raw = env_string(key);
    if raw.is_empty() {
        default
    } else {
        raw.eq_ignore_ascii_case("true")
    }
}

fn env_f64(key: &str, default: f64) -> f64 {
    env_string(key).parse().unwrap_or(default)
}

fn env_u32(key: &str, default: u32) -> u32 {
    env_string(key).parse().unwrap_or(default)
}

fn env_u64(key: &str, default: u64) -> u64 {
    env_string(key).parse().unwrap_or(default)
}
