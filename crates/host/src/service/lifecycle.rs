use std::fs;
use std::path::Path;
use std::sync::Arc;

use anyhow::{bail, Context, Result};
use serde_json::{json, Value};

use crate::container::{docker, ContainerLifetime, ContainerSpec, DaemonContainer};
use crate::daemon_wire::{
    response_is_accepted, ProtocolClient, DEFAULT_LAYER_STACK_ROOT, READY_OP,
};
use crate::service::registry::{
    SandboxRecord, SandboxRegistry, CREATED_BY_LABEL, SANDBOX_ID_LABEL, TCP_PORT_LABEL,
};

use super::args::validate_container_name;
use super::utils::{path_str, random_hex};
use super::{
    workspace_root_from_args, ManagedSandboxStart, SandboxHost, SandboxStatus,
    SANDBOX_OVERLAY_ROOT, SANDBOX_SCRATCH_TMPFS,
};

impl SandboxHost {
    pub fn open(config: super::HostConfig) -> Result<Self> {
        let config_yaml = fs::read_to_string(&config.config_yaml_path).with_context(|| {
            format!(
                "read daemon config document {}",
                config.config_yaml_path.display()
            )
        })?;
        let registry = Arc::new(SandboxRegistry::open(config.state_dir.clone())?);
        registry.rebuild_from_docker();
        Ok(Self {
            config,
            config_yaml,
            registry,
        })
    }

    pub fn acquire(&self) -> Result<String> {
        self.acquire_with_args(&json!({}))
    }

    pub fn acquire_with_args(&self, args: &Value) -> Result<String> {
        let sandbox_id = format!("sb-{}", random_hex(16)?);
        let (image, platform) = self.resolve_image_profile(args)?;
        let workspace_root = workspace_root_from_args(args)?;
        self.start_managed_sandbox(ManagedSandboxStart {
            sandbox_id,
            image,
            platform,
            workspace_root,
        })
    }

    pub(crate) fn start_managed_sandbox(&self, start: ManagedSandboxStart) -> Result<String> {
        let ManagedSandboxStart {
            sandbox_id,
            image,
            platform,
            workspace_root,
        } = start;
        validate_container_name(&sandbox_id)?;
        let token = random_hex(32)?;
        let forward_token = random_hex(32)?;
        let container = ContainerSpec {
            name: sandbox_id.clone(),
            image: image.clone(),
            platform: platform.clone(),
            privileged: self.config.docker_privileged,
            cap_add: Vec::new(),
            security_opt: Vec::new(),
            tmpfs: vec![SANDBOX_SCRATCH_TMPFS.to_owned()],
            labels: vec![
                (SANDBOX_ID_LABEL.to_owned(), sandbox_id.clone()),
                (TCP_PORT_LABEL.to_owned(), self.config.tcp_port.to_string()),
                (CREATED_BY_LABEL.to_owned(), self.config.created_by.clone()),
            ],
            lifetime: ContainerLifetime::Keep,
        };
        let mut daemon = self.config.daemon_spec(self.config.tcp_port);
        daemon.config_yaml = self.config_yaml.clone();
        let record = SandboxRecord::new_with_forward_token(
            sandbox_id.clone(),
            sandbox_id.clone(),
            token.clone(),
            forward_token.clone(),
            self.config.tcp_port,
            self.config.created_by.clone(),
            None,
        );
        let record = self.registry.insert(record)?;
        let started_container = match DaemonContainer::start_with_forward_token(
            &container,
            &daemon,
            token.clone(),
            forward_token.clone(),
        ) {
            Ok(started) => started,
            Err(err) => {
                self.registry.remove(&sandbox_id);
                let _ = docker(["rm", "-f", sandbox_id.as_str()]);
                return Err(err);
            }
        };
        record.cache_endpoint(started_container.client().addr());
        if let Err(err) = self.setup_managed_sandbox(&sandbox_id, &workspace_root) {
            self.registry.remove(&sandbox_id);
            let _ = docker(["rm", "-f", sandbox_id.as_str()]);
            return Err(err);
        }
        Ok(sandbox_id)
    }

    fn setup_managed_sandbox(&self, sandbox_id: &str, workspace_root: &Path) -> Result<()> {
        let workspace_root = path_str(workspace_root, "host.workspace_root")?;
        docker([
            "exec",
            "-w",
            "/",
            sandbox_id,
            "mkdir",
            "-p",
            workspace_root,
            SANDBOX_OVERLAY_ROOT,
        ])
        .with_context(|| format!("mkdir sandbox workspace {workspace_root}"))?;
        Ok(())
    }

    pub fn release(&self, sandbox_id: &str) -> bool {
        self.release_with_args(sandbox_id, &json!({}))
            .unwrap_or(false)
    }

    pub fn release_with_args(&self, sandbox_id: &str, _args: &Value) -> Result<bool> {
        let Some(record) = self.registry.get(sandbox_id) else {
            return Ok(false);
        };
        let docker_result = docker(["rm", "-f", record.container.as_str()]);
        match docker_result {
            Ok(_) => {
                self.registry.remove(sandbox_id);
                Ok(true)
            }
            Err(err) => Err(err.context(format!("remove sandbox container {}", record.container))),
        }
    }

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

    fn resolve_image_profile(&self, args: &Value) -> Result<(String, Option<String>)> {
        let profile = super::args::optional_string_arg(args, "image_profile").unwrap_or("default");
        if profile != "default" {
            bail!("unknown image_profile: {profile}");
        }
        Ok((self.config.image.clone(), self.config.platform.clone()))
    }

    pub(crate) fn probe_readiness(&self, record: &SandboxRecord) -> Value {
        let Some(endpoint) = record.cached_endpoint() else {
            return json!({"ready": false, "error": "endpoint not resolved"});
        };
        let client = ProtocolClient::new_forward_authorized(
            endpoint,
            Some(record.forward_token.clone()),
            self.config.request_timeout,
        );
        match client.request(
            READY_OP,
            "status-probe",
            &json!({"layer_stack_root": DEFAULT_LAYER_STACK_ROOT}),
        ) {
            Ok(resp) if response_is_accepted(&resp) => resp,
            Ok(resp) => json!({"ready": false, "error": resp}),
            Err(err) => json!({"ready": false, "error": err.to_string()}),
        }
    }
}
