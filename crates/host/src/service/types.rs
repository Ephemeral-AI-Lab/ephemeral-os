use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use serde_json::Value;

use crate::container::DaemonSpec;
use crate::service::registry::SandboxRegistry;

#[derive(Debug, Clone)]
pub struct HostConfig {
    pub image: String,
    pub platform: Option<String>,
    pub docker_privileged: bool,
    pub eosd_path: PathBuf,
    pub config_yaml_path: PathBuf,
    pub remote_daemon_dir: PathBuf,
    pub remote_eosd_path: PathBuf,
    pub remote_config_path: PathBuf,
    pub tcp_port: u16,
    pub ready_timeout: Duration,
    pub request_timeout: Duration,
    pub created_by: String,
    pub state_dir: PathBuf,
}

impl HostConfig {
    pub(crate) fn daemon_spec(&self, tcp_port: u16) -> DaemonSpec {
        DaemonSpec {
            eosd_path: self.eosd_path.clone(),
            remote_daemon_dir: self.remote_daemon_dir.clone(),
            remote_eosd_path: self.remote_eosd_path.clone(),
            remote_config_path: self.remote_config_path.clone(),
            config_yaml: String::new(),
            enable_layerstack_test_failpoints: false,
            extra_dirs: Vec::new(),
            tcp_port,
            ready_timeout: self.ready_timeout,
            request_timeout: self.request_timeout,
        }
    }
}

#[derive(Debug)]
pub struct SandboxStatus {
    pub sandbox_id: String,
    pub container: String,
    pub endpoint: Option<SocketAddr>,
    pub created_by: String,
    pub daemon: Value,
}

pub struct HostForwardRequest<'a> {
    pub sandbox_id: &'a str,
    pub op: &'a str,
    pub request_id: &'a str,
    pub args: &'a Value,
}

pub(crate) struct ManagedSandboxStart {
    pub(crate) sandbox_id: String,
    pub(crate) image: String,
    pub(crate) platform: Option<String>,
    pub(crate) workspace_root: PathBuf,
}

pub struct SandboxHost {
    pub(crate) config: HostConfig,
    pub(crate) config_yaml: String,
    pub(crate) registry: Arc<SandboxRegistry>,
}
