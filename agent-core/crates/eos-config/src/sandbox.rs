//! Sandbox provider configuration. agent-core is Docker-only (GC-eos-config-08):
//! the non-Docker provider config and its env bindings are not ported, and any
//! non-Docker `default_provider` value fails to deserialize.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

/// The sandbox backend. The seam is kept for future providers (spec-conventions
/// §4), but Docker is the only supported variant; any other provider string
/// fails to deserialize (`type-no-stringly`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
#[non_exhaustive]
pub enum SandboxProvider {
    /// The Docker sandbox backend (the only supported provider).
    #[default]
    Docker,
}

/// Docker-provider settings (`sections/sandbox.py:17-23`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
#[non_exhaustive]
pub struct DockerConfig {
    /// Connect to the docker daemon over TCP.
    pub daemon_tcp: bool,
    /// Run sandbox containers privileged.
    pub privileged: bool,
    /// Force-drop privileges (mutually exclusive with `privileged`).
    pub no_privilege: bool,
    /// Default container snapshot/image tag (empty means provider default).
    pub default_snapshot: String,
}

impl Default for DockerConfig {
    fn default() -> Self {
        Self {
            daemon_tcp: true,
            privileged: false,
            no_privilege: false,
            default_snapshot: String::new(),
        }
    }
}

/// Sandbox provider defaults and Docker-specific config (`sections/sandbox.py`).
///
/// Only host-side provisioning settings live here (provider selection + Docker
/// connect/launch options). Sandbox-execution timeouts are owned by the
/// ephemeral-os sandbox module (`sandbox/config/prd.yml`), not agent-core.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
#[non_exhaustive]
pub struct SandboxConfig {
    /// The default sandbox backend (Docker only in agent-core).
    pub default_provider: SandboxProvider,
    /// Docker-provider settings.
    pub docker: DockerConfig,
}

impl Default for SandboxConfig {
    fn default() -> Self {
        Self {
            default_provider: SandboxProvider::Docker,
            docker: DockerConfig::default(),
        }
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use super::*;

    // AC-eos-config-02 (sandbox subset): defaults match the Python source.
    #[test]
    fn test_sandbox_defaults() {
        let s = SandboxConfig::default();
        assert_eq!(s.default_provider, SandboxProvider::Docker);
        assert!(s.docker.daemon_tcp);
        assert!(!s.docker.privileged);
        assert!(!s.docker.no_privilege);
        assert_eq!(s.docker.default_snapshot, "");
    }

    // AC-eos-config-09 (partial): a non-Docker provider string fails to
    // deserialize (the Docker-only seam).
    #[test]
    fn test_non_docker_provider_rejected() {
        assert_eq!(
            serde_yaml::from_str::<SandboxProvider>("docker").unwrap(),
            SandboxProvider::Docker
        );
        assert!(serde_yaml::from_str::<SandboxProvider>("podman").is_err());
    }
}
