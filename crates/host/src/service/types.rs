use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use serde_json::Value;
use trace::{RequestId, TraceId};

use crate::container::DaemonSpec;
use crate::service::registry::SandboxRegistry;
use crate::trace_store::TraceStore;

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

#[derive(Debug, Clone)]
pub struct ForwardTraceContext {
    pub trace_id: TraceId,
    pub request_id: RequestId,
    pub parent_span_id: Option<u64>,
    pub(crate) gateway_events: Vec<ForwardTraceEvent>,
}

impl ForwardTraceContext {
    #[must_use]
    pub fn new(invocation_id: &str) -> Self {
        Self {
            trace_id: TraceId::new(),
            request_id: RequestId::parse(invocation_id.to_owned()).unwrap_or_default(),
            parent_span_id: None,
            gateway_events: Vec::new(),
        }
    }

    pub fn push_gateway_event(&mut self, module: &str, event: &str, details: Value) {
        self.gateway_events.push(ForwardTraceEvent {
            module: module.to_owned(),
            event: event.to_owned(),
            details,
        });
    }
}

#[derive(Debug, Clone)]
pub(crate) struct ForwardTraceEvent {
    pub(crate) module: String,
    pub(crate) event: String,
    pub(crate) details: Value,
}

pub struct HostForwardRequest<'a> {
    pub sandbox_id: &'a str,
    pub mutates_state: bool,
    pub op: &'a str,
    pub invocation_id: &'a str,
    pub args: &'a Value,
    pub trace: ForwardTraceContext,
}

pub(crate) struct ManagedSandboxStart {
    pub(crate) sandbox_id: String,
    pub(crate) image: String,
    pub(crate) platform: Option<String>,
    pub(crate) workspace_root: PathBuf,
    pub(crate) response_op: &'static str,
}

pub struct SandboxHost {
    pub(crate) config: HostConfig,
    pub(crate) config_yaml: String,
    pub(crate) registry: Arc<SandboxRegistry>,
    pub(crate) trace_store: Arc<TraceStore>,
}
