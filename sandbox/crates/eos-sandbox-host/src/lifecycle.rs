//! `SandboxHost` — the host engine facade: provision (SPEC §5), destroy,
//! status, list, and the forward entry point.

use std::fs;
use std::net::SocketAddr;
use std::path::PathBuf;
use std::time::Duration;

use anyhow::{Context, Result};
use serde_json::{json, Value};

use crate::client::{is_success, ProtocolClient};
use crate::container::{ContainerLifetime, ContainerSpec, DaemonContainer, DaemonSpec};
use crate::docker::docker;
use crate::forward;
use crate::recovery::ForwardError;
use crate::registry::{
    SandboxRecord, SandboxRegistry, CREATED_BY_LABEL, SANDBOX_ID_LABEL, TCP_PORT_LABEL,
};
use crate::wire::{DEFAULT_LAYER_STACK_ROOT, READY_OP};

/// Engine configuration (one fleet, one image).
#[derive(Debug, Clone)]
pub struct HostConfig {
    /// Image every sandbox runs.
    pub image: String,
    /// Optional `--platform` (e.g. `linux/amd64`).
    pub platform: Option<String>,
    /// Host path of the static `eosd` binary uploaded into each sandbox.
    pub eosd_path: PathBuf,
    /// Host path of the daemon config document uploaded into each sandbox.
    pub config_yaml_path: PathBuf,
    /// In-container daemon state dir.
    pub remote_daemon_dir: PathBuf,
    /// In-container `eosd` path.
    pub remote_eosd_path: PathBuf,
    /// In-container path the daemon loads its config from (compiled into the
    /// `eosd` build).
    pub remote_config_path: PathBuf,
    /// In-container daemon TCP port (published to loopback by docker).
    pub tcp_port: u16,
    /// Provision/respawn ready-gate budget.
    pub ready_timeout: Duration,
    /// Per-request socket timeout.
    pub request_timeout: Duration,
    /// Identity stamped into the `eos.created_by` container label.
    pub created_by: String,
    /// Host-private dir holding sandbox auth tokens (`<id>.token`).
    pub state_dir: PathBuf,
}

impl HostConfig {
    /// The daemon bring-up spec for one sandbox.
    pub(crate) fn daemon_spec(&self, tcp_port: u16) -> DaemonSpec {
        DaemonSpec {
            eosd_path: self.eosd_path.clone(),
            remote_daemon_dir: self.remote_daemon_dir.clone(),
            remote_eosd_path: self.remote_eosd_path.clone(),
            remote_config_path: self.remote_config_path.clone(),
            config_yaml: String::new(),
            extra_dirs: Vec::new(),
            tcp_port,
            ready_timeout: self.ready_timeout,
            request_timeout: self.request_timeout,
        }
    }
}

/// Host view of one sandbox (the `sandbox.status` payload source).
#[derive(Debug)]
pub struct SandboxStatus {
    /// Public sandbox identity.
    pub sandbox_id: String,
    /// Docker container name.
    pub container: String,
    /// Cached loopback endpoint, when resolved.
    pub endpoint: Option<SocketAddr>,
    /// Provisioning identity.
    pub created_by: String,
    /// The daemon's `sandbox.runtime.ready` response (or a synthesized
    /// `{"ready": false, ...}` when unreachable).
    pub daemon: Value,
}

/// The host engine: owns and reaches sandboxes.
pub struct SandboxHost {
    config: HostConfig,
    config_yaml: String,
    registry: SandboxRegistry,
}

impl SandboxHost {
    /// Open the engine: load the daemon config document, open the registry,
    /// and rebuild it from docker labels (a host restart never orphans
    /// running sandboxes).
    ///
    /// # Errors
    /// Returns an error when the config document or state dir is unusable.
    pub fn open(config: HostConfig) -> Result<Self> {
        let config_yaml = fs::read_to_string(&config.config_yaml_path).with_context(|| {
            format!(
                "read daemon config document {}",
                config.config_yaml_path.display()
            )
        })?;
        let registry = SandboxRegistry::open(config.state_dir.clone())?;
        registry.rebuild_from_docker();
        Ok(Self {
            config,
            config_yaml,
            registry,
        })
    }

    /// Provision one sandbox (SPEC §5) and return its id.
    ///
    /// # Errors
    /// Returns an error if any provision step fails; the container is removed
    /// on bring-up failure.
    pub fn acquire(&self) -> Result<String> {
        let sandbox_id = format!("sb-{}", random_hex(16)?);
        let token = random_hex(32)?;
        let container = ContainerSpec {
            name: sandbox_id.clone(),
            image: self.config.image.clone(),
            platform: self.config.platform.clone(),
            cap_add: Vec::new(),
            security_opt: Vec::new(),
            tmpfs: Vec::new(),
            labels: vec![
                (SANDBOX_ID_LABEL.to_owned(), sandbox_id.clone()),
                (TCP_PORT_LABEL.to_owned(), self.config.tcp_port.to_string()),
                (CREATED_BY_LABEL.to_owned(), self.config.created_by.clone()),
            ],
            lifetime: ContainerLifetime::Keep,
        };
        let mut daemon = self.config.daemon_spec(self.config.tcp_port);
        daemon.config_yaml = self.config_yaml.clone();
        let started = match DaemonContainer::start(&container, &daemon, token.clone()) {
            Ok(started) => started,
            Err(err) => {
                // Keep-lifetime containers survive drop; reap the failed one.
                let _ = docker(&["rm".to_owned(), "-f".to_owned(), sandbox_id.clone()]);
                return Err(err);
            }
        };
        let record = SandboxRecord::new(
            sandbox_id.clone(),
            sandbox_id.clone(),
            token,
            self.config.tcp_port,
            self.config.created_by.clone(),
            Some(started.client().addr()),
        );
        self.registry.insert(record)?;
        Ok(sandbox_id)
    }

    /// Destroy one sandbox: `docker rm -f`, drop the record. No daemon-side
    /// courtesy calls — container teardown IS the cleanup. Returns `false`
    /// when the sandbox is not in the registry.
    pub fn release(&self, sandbox_id: &str) -> bool {
        let Some(record) = self.registry.remove(sandbox_id) else {
            return false;
        };
        let _ = docker(&["rm".to_owned(), "-f".to_owned(), record.container.clone()]);
        true
    }

    /// Host view of one sandbox plus embedded daemon readiness.
    #[must_use]
    pub fn status(&self, sandbox_id: &str) -> Option<SandboxStatus> {
        let record = self.registry.get(sandbox_id)?;
        let daemon = self.probe_readiness(&record);
        Some(SandboxStatus {
            sandbox_id: record.sandbox_id.clone(),
            container: record.container.clone(),
            endpoint: record.cached_endpoint(),
            created_by: record.created_by.clone(),
            daemon,
        })
    }

    /// Enumerate the registry.
    #[must_use]
    pub fn list(&self) -> Vec<SandboxStatus> {
        self.registry
            .list()
            .into_iter()
            .map(|record| SandboxStatus {
                sandbox_id: record.sandbox_id.clone(),
                container: record.container.clone(),
                endpoint: record.cached_endpoint(),
                created_by: record.created_by.clone(),
                daemon: Value::Null,
            })
            .collect()
    }

    /// Whether `sandbox_id` is registered.
    #[must_use]
    pub fn knows(&self, sandbox_id: &str) -> bool {
        self.registry.get(sandbox_id).is_some()
    }

    /// Forward one daemon-bound request, running the SPEC §6 recovery ladder
    /// on failure. Returns `None` when the sandbox is unknown.
    pub fn forward(
        &self,
        sandbox_id: &str,
        mutates_state: bool,
        op: &str,
        invocation_id: &str,
        args: &Value,
    ) -> Option<Result<Value, ForwardError>> {
        let record = self.registry.get(sandbox_id)?;
        Some(forward::forward(
            &record,
            &self.config,
            mutates_state,
            op,
            invocation_id,
            args,
        ))
    }

    /// One bounded `sandbox.runtime.ready` probe (status embedding only — the
    /// readiness op requires a `layer_stack_root`; the default root is used).
    fn probe_readiness(&self, record: &SandboxRecord) -> Value {
        let Some(endpoint) = record.cached_endpoint() else {
            return json!({"ready": false, "error": "endpoint not resolved"});
        };
        let client = ProtocolClient::new(
            endpoint,
            Some(record.token.clone()),
            self.config.request_timeout,
        );
        match client.request_unstamped(
            READY_OP,
            "status-probe",
            &json!({"layer_stack_root": DEFAULT_LAYER_STACK_ROOT}),
        ) {
            Ok(resp) if is_success(&resp) => resp,
            Ok(resp) => json!({"ready": false, "error": resp}),
            Err(err) => json!({"ready": false, "error": err.to_string()}),
        }
    }
}

/// Fresh randomness from the OS for sandbox ids and auth tokens.
fn random_hex(bytes: usize) -> Result<String> {
    use std::io::Read;

    let mut buf = vec![0_u8; bytes];
    fs::File::open("/dev/urandom")
        .context("open /dev/urandom")?
        .read_exact(&mut buf)
        .context("read /dev/urandom")?;
    Ok(buf.iter().map(|byte| format!("{byte:02x}")).collect())
}
