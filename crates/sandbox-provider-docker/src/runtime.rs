//! `SandboxRuntime` over bollard: create a stopped Linux container, remove it,
//! and recover existing containers by label after a gateway restart.

use std::collections::HashMap;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

use sandbox_config::configs::manager::DockerRuntimeConfig;
use sandbox_config::configs::runtime::RuntimeConfig;
use sandbox_manager::{
    CreateSandboxRequest, CreateSandboxResult, ManagerError, SandboxDaemonEndpoint, SandboxId,
    SandboxRecord, SandboxRuntime, SandboxState,
};

use crate::engine::{ContainerSpec, DockerEngine, DockerError, VolumeSpec};
use crate::labels;
use crate::launch::daemon_launch_argv;

const ENDPOINT_HOST: &str = "127.0.0.1";
/// Docker-backed runtime. Creates stopped containers; the installer starts them.
pub struct DockerSandboxRuntime {
    engine: DockerEngine,
}

impl DockerSandboxRuntime {
    /// Build a runtime from the resolved Docker config.
    #[must_use]
    pub fn new(config: DockerRuntimeConfig) -> Self {
        Self {
            engine: DockerEngine::new(config),
        }
    }

    /// Rebuild manager records for containers owned by this gateway instance.
    ///
    /// # Errors
    /// Returns an error when the Docker Engine cannot be queried.
    pub fn recover_sandboxes(&self) -> Result<Vec<SandboxRecord>, ManagerError> {
        let config = self.engine.config();
        let recovered = self
            .engine
            .list_recoverable(config.gateway_instance_id.clone(), config.daemon_port)
            .map_err(runtime_failed)?;
        let mut records = Vec::with_capacity(recovered.len());
        for container in recovered {
            let Ok(id) = SandboxId::new(container.sandbox_id) else {
                continue;
            };
            let endpoint = SandboxDaemonEndpoint::new(
                ENDPOINT_HOST,
                container.published_port,
                container.auth_token,
            );
            records.push(SandboxRecord {
                id,
                workspace_root: PathBuf::from(container.host_workspace_root),
                state: SandboxState::Ready,
                daemon: Some(endpoint),
            });
        }
        Ok(records)
    }
}

impl SandboxRuntime for DockerSandboxRuntime {
    fn create_sandbox(
        &self,
        request: &CreateSandboxRequest,
    ) -> Result<CreateSandboxResult, ManagerError> {
        let config = self.engine.config();
        let name = format!("eos-{}", uuid::Uuid::new_v4());
        let id = SandboxId::new(name.clone()).map_err(|error| ManagerError::RuntimeFailed {
            message: format!("generated container name is invalid: {error}"),
        })?;
        let auth_token = uuid::Uuid::new_v4().to_string();
        let record = SandboxRecord::new(
            id.clone(),
            request.workspace_root.clone(),
            SandboxState::Creating,
        );
        let labels = build_labels(config, &id, &auth_token, &request.workspace_root);
        let workspace_scratch_root = workspace_scratch_root(config)?;
        let workspace_scratch_volume = workspace_scratch_volume_name(&id);
        let cmd = daemon_launch_argv(config, &record, &auth_token);
        let spec = ContainerSpec {
            name,
            image: resolve_image(config, &request.image),
            cmd,
            env: config.container_env.clone(),
            labels: labels.clone(),
            binds: vec![format!(
                "{}:{}",
                request.workspace_root.display(),
                config.container_workspace_root.display()
            )],
            volumes: vec![VolumeSpec {
                name: workspace_scratch_volume,
                target: workspace_scratch_root.to_string_lossy().into_owned(),
                labels: build_volume_labels(config, &id),
            }],
            daemon_port: config.daemon_port,
            privileged: config.privileged,
            platform: config.platform.clone(),
            memory_bytes: config.memory_bytes,
            nano_cpus: config.nano_cpus,
        };
        self.engine.create_container(spec).map_err(runtime_failed)?;
        Ok(CreateSandboxResult { id })
    }

    fn destroy_sandbox(&self, record: &SandboxRecord) -> Result<(), ManagerError> {
        self.engine
            .remove_container(record.id.as_str().to_owned())
            .map_err(runtime_failed)?;
        self.engine
            .remove_volume(workspace_scratch_volume_name(&record.id))
            .map_err(runtime_failed)
    }
}

fn workspace_scratch_root(config: &DockerRuntimeConfig) -> Result<PathBuf, ManagerError> {
    let document = sandbox_config::load_path(&config.daemon_config_yaml_path)
        .map_err(runtime_config_failed)?;
    let runtime = document
        .section::<RuntimeConfig>("runtime")
        .map_err(runtime_config_failed)?;
    runtime
        .validate()
        .map_err(|error| ManagerError::RuntimeFailed {
            message: format!("invalid daemon runtime config: {error}"),
        })?;
    Ok(runtime.workspace.scratch_root)
}

fn workspace_scratch_volume_name(id: &SandboxId) -> String {
    format!("{}-workspace", id.as_str())
}

fn resolve_image(config: &DockerRuntimeConfig, requested: &str) -> String {
    if requested.trim().is_empty() {
        config
            .default_image
            .clone()
            .unwrap_or_else(|| requested.to_owned())
    } else {
        requested.to_owned()
    }
}

fn build_labels(
    config: &DockerRuntimeConfig,
    id: &SandboxId,
    auth_token: &str,
    host_workspace_root: &std::path::Path,
) -> HashMap<String, String> {
    let created_at = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|elapsed| elapsed.as_secs())
        .unwrap_or_default();
    HashMap::from([
        (labels::SANDBOX_ID.to_owned(), id.as_str().to_owned()),
        (
            labels::GATEWAY_INSTANCE_ID.to_owned(),
            config.gateway_instance_id.clone(),
        ),
        (labels::AUTH_TOKEN.to_owned(), auth_token.to_owned()),
        (
            labels::DAEMON_PORT.to_owned(),
            config.daemon_port.to_string(),
        ),
        (
            labels::HOST_WORKSPACE_ROOT.to_owned(),
            host_workspace_root.to_string_lossy().into_owned(),
        ),
        (
            labels::CONTAINER_WORKSPACE_ROOT.to_owned(),
            config
                .container_workspace_root
                .to_string_lossy()
                .into_owned(),
        ),
        (labels::CREATED_AT.to_owned(), created_at.to_string()),
        (
            labels::CLEANUP_POLICY.to_owned(),
            labels::CLEANUP_POLICY_REMOVE_ON_DESTROY.to_owned(),
        ),
    ])
}

fn build_volume_labels(config: &DockerRuntimeConfig, id: &SandboxId) -> HashMap<String, String> {
    HashMap::from([
        (labels::SANDBOX_ID.to_owned(), id.as_str().to_owned()),
        (
            labels::GATEWAY_INSTANCE_ID.to_owned(),
            config.gateway_instance_id.clone(),
        ),
        (
            labels::CLEANUP_POLICY.to_owned(),
            labels::CLEANUP_POLICY_REMOVE_ON_DESTROY.to_owned(),
        ),
    ])
}

fn runtime_failed(error: DockerError) -> ManagerError {
    ManagerError::RuntimeFailed {
        message: error.to_string(),
    }
}

fn runtime_config_failed(error: sandbox_config::ConfigError) -> ManagerError {
    ManagerError::RuntimeFailed {
        message: format!("failed to load daemon runtime config: {error}"),
    }
}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::time::{SystemTime, UNIX_EPOCH};

    use super::*;

    #[test]
    fn workspace_scratch_volume_name_is_derived_from_sandbox_id() {
        let id = SandboxId::new("eos-abc").expect("valid id");

        assert_eq!(workspace_scratch_volume_name(&id), "eos-abc-workspace");
    }

    #[test]
    fn workspace_scratch_volume_labels_do_not_include_auth_token() {
        let config = DockerRuntimeConfig {
            gateway_instance_id: "gateway-1".to_owned(),
            ..DockerRuntimeConfig::default()
        };
        let id = SandboxId::new("eos-abc").expect("valid id");

        let labels = build_volume_labels(&config, &id);

        assert_eq!(
            labels.get(crate::labels::SANDBOX_ID),
            Some(&"eos-abc".to_owned())
        );
        assert_eq!(
            labels.get(crate::labels::GATEWAY_INSTANCE_ID),
            Some(&"gateway-1".to_owned())
        );
        assert!(!labels.contains_key(crate::labels::AUTH_TOKEN));
    }

    #[test]
    fn workspace_scratch_root_reads_daemon_runtime_config() {
        let path = temp_config_path();
        fs::write(
            &path,
            r#"
runtime:
  workspace:
    layer_stack_root: /eos/layer-stack
    scratch_root: /custom/workspace
    setup_timeout_s: 30
    exit_grace_s: 0.25
    rfc1918_egress: allow
  namespace_execution:
    scratch_root: /eos/namespace_execution
"#,
        )
        .expect("write config");
        let config = DockerRuntimeConfig {
            daemon_config_yaml_path: path.clone(),
            ..DockerRuntimeConfig::default()
        };

        let scratch_root = workspace_scratch_root(&config).expect("scratch root loads");

        assert_eq!(scratch_root, PathBuf::from("/custom/workspace"));
        let _ = fs::remove_file(path);
    }

    fn temp_config_path() -> PathBuf {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("time after epoch")
            .as_nanos();
        std::env::temp_dir().join(format!(
            "eos-docker-runtime-config-{nanos}-{}.yml",
            std::process::id()
        ))
    }
}
