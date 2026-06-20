use anyhow::{bail, Context, Result};
use serde_json::{json, Value};

use crate::container::{docker, resolve_published_addr};
use crate::daemon_wire::response_is_accepted;

use super::args::{
    optional_string_arg, optional_u16_arg, required_string_arg, validate_container_name,
};
use super::docker_json::{mark_managed_containers, parse_json_lines};
use super::utils::random_hex;
use super::{workspace_root_from_args, ManagedSandboxStart, SandboxHost};

impl SandboxHost {
    pub fn container_list(&self, _args: &Value) -> Result<Value> {
        docker(["ps", "-a", "--format", "{{json .}}"]).and_then(|out| {
            let mut containers = parse_json_lines(&out)?;
            mark_managed_containers(&mut containers, &self.registry.list());
            Ok(json!({"containers": containers}))
        })
    }

    pub fn container_start(&self, args: &Value) -> Result<Value> {
        let image = required_string_arg(args, "image")?;
        let platform = optional_string_arg(args, "platform")
            .map(str::to_owned)
            .or_else(|| self.config.platform.clone());
        let workspace_root = workspace_root_from_args(args)?;
        let sandbox_id = match optional_string_arg(args, "name") {
            Some(name) => name.to_owned(),
            None => format!("sb-{}", random_hex(16)?),
        };
        let sandbox_id = self.start_managed_sandbox(ManagedSandboxStart {
            sandbox_id,
            image: image.to_owned(),
            platform: platform.clone(),
            workspace_root,
        })?;
        Ok(json!({
            "sandbox_id": sandbox_id,
            "container": sandbox_id,
            "image": image,
            "platform": platform,
        }))
    }

    pub fn container_adopt(&self, args: &Value) -> Result<Value> {
        let container = required_string_arg(args, "container")?;
        let sandbox_id = optional_string_arg(args, "sandbox_id").unwrap_or(container);
        validate_container_name(sandbox_id)?;
        let tcp_port = optional_u16_arg(args, "tcp_port")?.unwrap_or(self.config.tcp_port);
        let token = optional_string_arg(args, "auth_token")
            .map(str::to_owned)
            .or_else(|| self.registry.load_token(sandbox_id).ok())
            .with_context(|| {
                format!("auth_token is required when no persisted token exists for {sandbox_id}")
            })?;
        let forward_token = optional_string_arg(args, "forward_auth_token")
            .map(str::to_owned)
            .or_else(|| self.registry.load_forward_token(sandbox_id).ok())
            .unwrap_or_else(|| token.clone());
        let endpoint = resolve_published_addr(container, tcp_port)?
            .with_context(|| format!("no published port {tcp_port} for container {container}"))?;
        let record = super::registry::SandboxRecord::new_with_forward_token(
            sandbox_id.to_owned(),
            container.to_owned(),
            token,
            forward_token,
            tcp_port,
            self.config.created_by.clone(),
            Some(endpoint),
        );
        let record = self.registry.insert(record)?;
        let readiness = self.probe_readiness(&record);
        if !response_is_accepted(&readiness) {
            self.registry.remove(sandbox_id);
            bail!("container {container} daemon readiness failed: {readiness}");
        }
        Ok(json!({
            "sandbox_id": sandbox_id,
            "container": container,
            "endpoint": endpoint.to_string(),
            "daemon": readiness,
        }))
    }

    pub fn container_stop(&self, args: &Value) -> Result<Value> {
        let target = self.resolve_container_target(args)?;
        docker(["stop", target.container.as_str()])?;
        Ok(json!({
            "container": target.container,
            "sandbox_id": target.sandbox_id,
            "stopped": true,
        }))
    }

    pub fn container_remove(&self, args: &Value) -> Result<Value> {
        let target = self.resolve_container_target(args)?;
        docker(["rm", "-f", target.container.as_str()])?;
        if let Some(sandbox_id) = &target.sandbox_id {
            self.registry.remove(sandbox_id);
        }
        Ok(json!({
            "container": target.container,
            "sandbox_id": target.sandbox_id,
            "removed": true,
        }))
    }

    fn resolve_container_target(&self, args: &Value) -> Result<HostContainerTarget> {
        if let Some(sandbox_id) = optional_string_arg(args, "sandbox_id") {
            let record = self
                .registry
                .get(sandbox_id)
                .with_context(|| format!("unknown sandbox_id: {sandbox_id}"))?;
            return Ok(HostContainerTarget {
                sandbox_id: Some(sandbox_id.to_owned()),
                container: record.container.clone(),
            });
        }
        let container = required_string_arg(args, "container")?;
        validate_container_name(container)?;
        let sandbox_id = self
            .registry
            .list()
            .into_iter()
            .find(|record| record.container == container)
            .map(|record| record.sandbox_id.clone());
        Ok(HostContainerTarget {
            sandbox_id,
            container: container.to_owned(),
        })
    }
}

struct HostContainerTarget {
    sandbox_id: Option<String>,
    container: String,
}
