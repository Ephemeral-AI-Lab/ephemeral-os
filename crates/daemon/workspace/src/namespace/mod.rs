mod cgroup;
mod fds;
mod holder;
mod setns_runner;

#[cfg(target_os = "linux")]
pub(crate) use setns_runner::{ns_runner_request, run_child};

#[cfg(test)]
use std::sync::Arc;

use crate::profile::IsolatedNetworkError;

pub(crate) const TEST_HARNESS_ENV: &str = "EOS_ISOLATED_WORKSPACE_TEST_HARNESS";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum NamespaceNetwork {
    Host,
    IsolatedNetwork,
}

impl NamespaceNetwork {
    #[cfg(target_os = "linux")]
    pub(crate) const fn holder_arg(self) -> &'static str {
        match self {
            Self::Host => "host",
            Self::IsolatedNetwork => "isolated",
        }
    }

    pub(crate) const fn requires_net_fd(self) -> bool {
        matches!(self, Self::IsolatedNetwork)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct NamespacePlan {
    pub(crate) user: bool,
    pub(crate) mount: bool,
    pub(crate) pid: bool,
    pub(crate) network: NamespaceNetwork,
}

impl NamespacePlan {
    pub(crate) const fn host_workspace() -> Self {
        Self {
            user: true,
            mount: true,
            pid: true,
            network: NamespaceNetwork::Host,
        }
    }

    pub(crate) const fn isolated() -> Self {
        Self {
            user: true,
            mount: true,
            pid: true,
            network: NamespaceNetwork::IsolatedNetwork,
        }
    }

    pub(crate) fn fd_names(self) -> Vec<&'static str> {
        let mut names = Vec::with_capacity(4);
        if self.user {
            names.push("user");
        }
        if self.mount {
            names.push("mnt");
        }
        if self.pid {
            names.push("pid");
        }
        if self.network.requires_net_fd() {
            names.push("net");
        }
        names
    }
}

pub(crate) fn setup_error(error: impl std::fmt::Display) -> IsolatedNetworkError {
    IsolatedNetworkError::SetupFailed {
        step: error.to_string(),
    }
}

pub(crate) fn test_harness_enabled() -> bool {
    std::env::var(TEST_HARNESS_ENV)
        .is_ok_and(|value| matches!(value.trim(), "1" | "true" | "TRUE" | "yes" | "YES"))
}

pub(crate) struct NamespaceRuntime {
    pub(crate) stub: bool,
    #[cfg(test)]
    pub(crate) stub_holder_pid: i32,
    #[cfg(test)]
    pub(crate) killed_holders: Option<Arc<std::sync::Mutex<Vec<i32>>>>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub(crate) struct HolderKillReport {
    pub(crate) holder_was_alive: bool,
    pub(crate) exit_status: Option<i32>,
    pub(crate) signal: Option<i32>,
    pub(crate) status_raw: Option<i32>,
}

impl NamespaceRuntime {
    pub(crate) fn from_env() -> Self {
        Self {
            stub: test_harness_enabled(),
            #[cfg(test)]
            stub_holder_pid: 0,
            #[cfg(test)]
            killed_holders: None,
        }
    }

    pub(crate) fn stubbed() -> Self {
        Self {
            stub: true,
            #[cfg(test)]
            stub_holder_pid: 0,
            #[cfg(test)]
            killed_holders: None,
        }
    }
}
