//! Host-neutral `sandbox.plugin.ensure` argument parsing: a manifest + caller args
//! become a [`ParsedEnsure`] (operation routes + service process specs). Reading
//! the PPC socket root from the daemon runtime config stays daemon-side and is
//! threaded in as `ppc_socket_root`; everything here is pure on its inputs.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use serde_json::Value;

use super::contract::{PluginAuditFields, PluginEnsureInput};
use super::route::{PluginOperationRoute, PluginProcessSpec};
use super::PpcError;
use plugin::{
    PluginError, PluginManifest, PluginServiceKey, PluginServiceKeyParts, PluginServiceManifest,
    PluginServiceState, PluginServiceStatus, ServiceMode,
};

use super::package::{package_roots, PackageRoots};

pub fn validate_plugin_caller_fields(args: &Value) -> Result<(), PluginError> {
    PluginAuditFields::parse(args)
        .map(|_| ())
        .map_err(|err| PluginError::Ppc(err.message()))
}

/// Result of parsing one `sandbox.plugin.ensure` call: routes + service specs the
/// daemon registers into its live `LoadedPluginRuntime`.
pub struct ParsedEnsure {
    pub plugin_id: String,
    pub plugin_digest: String,
    pub manifest: Option<PluginManifest>,
    pub registered_ops: Vec<String>,
    pub operation_routes: BTreeMap<String, PluginOperationRoute>,
    pub services: Vec<PluginServiceStatus>,
    pub service_processes: Vec<PluginProcessSpec>,
    pub runtime_loaded: bool,
}

impl ParsedEnsure {
    pub fn from_input(input: &PluginEnsureInput, ppc_socket_root: &str) -> Result<Self, PpcError> {
        if let Some(manifest_value) = &input.manifest {
            let manifest: PluginManifest = serde_json::from_value(manifest_value.clone())
                .map_err(|err| PluginError::Manifest(err.to_string()))?;
            manifest.validate()?;
            return Self::from_manifest(input, manifest, ppc_socket_root);
        }

        let plugin_id = input
            .plugin
            .as_deref()
            .unwrap_or_default()
            .trim()
            .to_owned();
        validate_public_identifier("plugin", &plugin_id)?;
        let plugin_digest = input
            .digest
            .as_deref()
            .unwrap_or_default()
            .trim()
            .to_owned();
        Ok(Self {
            plugin_id,
            plugin_digest,
            manifest: None,
            registered_ops: Vec::new(),
            operation_routes: BTreeMap::new(),
            services: Vec::new(),
            service_processes: Vec::new(),
            runtime_loaded: false,
        })
    }

    fn from_manifest(
        input: &PluginEnsureInput,
        manifest: PluginManifest,
        ppc_socket_root: &str,
    ) -> Result<Self, PpcError> {
        let manifest_for_package = manifest.clone();
        let package_roots = package_roots(&input.package, &manifest)?;
        let layer_stack_root = input.layer_stack_root.clone();
        let service_keys = service_keys_for_manifest(input, &manifest)?;
        let operation_routes = operation_routes_for_manifest(
            &manifest,
            &service_keys,
            layer_stack_root.as_deref(),
            &package_roots,
        );
        let registered_ops = operation_routes.keys().cloned().collect::<Vec<_>>();
        let (services, service_processes) = services_for_manifest(
            &manifest,
            &service_keys,
            &registered_ops,
            ppc_socket_root,
            &package_roots,
        )?;
        Ok(Self {
            plugin_id: manifest.plugin_id,
            plugin_digest: manifest.plugin_digest,
            manifest: Some(manifest_for_package),
            registered_ops,
            operation_routes,
            services,
            service_processes,
            runtime_loaded: true,
        })
    }
}

fn operation_routes_for_manifest(
    manifest: &PluginManifest,
    service_keys: &BTreeMap<String, PluginServiceKey>,
    layer_stack_root: Option<&str>,
    package_roots: &PackageRoots,
) -> BTreeMap<String, PluginOperationRoute> {
    manifest
        .operations
        .iter()
        .map(|op| {
            let public_op = public_op_name(&manifest.plugin_id, &op.op_name);
            let service = op.service_id.as_ref().and_then(|service_id| {
                manifest
                    .services
                    .iter()
                    .find(|service| service.service_id == *service_id)
            });
            let service_key = op
                .service_id
                .as_ref()
                .and_then(|service_id| service_keys.get(service_id))
                .cloned();
            (
                public_op.clone(),
                PluginOperationRoute {
                    plugin_id: manifest.plugin_id.clone(),
                    op_name: op.op_name.clone(),
                    public_op,
                    layer_stack_root: layer_stack_root.map(str::to_owned),
                    intent: op.intent,
                    auto_workspace_overlay: op.auto_workspace_overlay,
                    service_id: op.service_id.clone(),
                    service_instance_id: service_key
                        .as_ref()
                        .map(PluginServiceKey::service_instance_id),
                    service_key,
                    service_mode: service.map(|service| service.service_mode),
                    service_command: service
                        .map(|service| resolved_service_command(service, package_roots))
                        .unwrap_or_default(),
                    service_ppc_protocol_version: service
                        .map(|service| service.ppc_protocol_version),
                    timeout_ms: op.timeout_ms,
                },
            )
        })
        .collect()
}

/// Build the public op name the daemon dispatcher registers: `plugin.<plugin>.<op>`.
fn public_op_name(plugin_name: &str, op_name: &str) -> String {
    format!("plugin.{plugin_name}.{op_name}")
}

fn services_for_manifest(
    manifest: &PluginManifest,
    service_keys: &BTreeMap<String, PluginServiceKey>,
    registered_ops: &[String],
    ppc_socket_root: &str,
    package_roots: &PackageRoots,
) -> Result<(Vec<PluginServiceStatus>, Vec<PluginProcessSpec>), PluginError> {
    if manifest.services.is_empty() {
        return Ok((Vec::new(), Vec::new()));
    }
    let mut process_specs = Vec::new();
    let statuses = manifest
        .services
        .iter()
        .map(|service| {
            let key = service_keys
                .get(&service.service_id)
                .ok_or_else(|| {
                    PluginError::Manifest(format!(
                        "service {} key was not prepared",
                        service.service_id
                    ))
                })?
                .clone();
            let mut status = PluginServiceStatus::new(key.clone());
            status.state = PluginServiceState::Stopped;
            status.registered_ops.clone_from(&registered_ops.to_vec());
            status.last_error = Some(service_initial_status_message(service.service_mode));
            if service.service_mode == ServiceMode::WorkspaceSnapshotRefresh
                && !service.command.is_empty()
            {
                process_specs.push(process_spec(&key, service, ppc_socket_root, package_roots)?);
            }
            Ok(status)
        })
        .collect::<Result<Vec<_>, PluginError>>()?;
    Ok((statuses, process_specs))
}

fn service_initial_status_message(service_mode: ServiceMode) -> String {
    match service_mode {
        ServiceMode::OneshotOverlay => "oneshot overlay worker starts per operation".to_owned(),
        // `ServiceMode` is non-exhaustive contract-side; every process-backed
        // mode starts in the not-yet-started state.
        _ => "process-backed PPC execution is not started".to_owned(),
    }
}

fn process_spec(
    key: &PluginServiceKey,
    service: &PluginServiceManifest,
    ppc_socket_root: &str,
    package_roots: &PackageRoots,
) -> Result<PluginProcessSpec, PluginError> {
    let working_dir = service_working_dir(service, package_roots);
    PluginProcessSpec::new_with_package_paths(
        key.clone(),
        resolved_service_command(service, package_roots),
        package_roots.package_root.clone(),
        package_roots.dependency_root.clone(),
        working_dir,
        service.ppc_protocol_version,
        ppc_socket_root,
    )
}

fn resolved_service_command(
    service: &PluginServiceManifest,
    package_roots: &PackageRoots,
) -> Vec<String> {
    let mut command = service.command.clone();
    if let Some(program) = command.first_mut() {
        if let Some(path) = resolve_package_relative_executable(
            program,
            &service_working_dir(service, package_roots),
        ) {
            *program = path.to_string_lossy().into_owned();
        }
    }
    command
}

fn service_working_dir(service: &PluginServiceManifest, package_roots: &PackageRoots) -> PathBuf {
    match service.working_dir.as_deref() {
        None | Some(".") => package_roots.package_root.clone(),
        Some(working_dir) => package_roots.package_root.join(working_dir),
    }
}

fn resolve_package_relative_executable(program: &str, working_dir: &Path) -> Option<PathBuf> {
    let path = Path::new(program);
    if path.is_absolute() {
        None
    } else if program.contains('/') {
        let mut resolved = working_dir.to_path_buf();
        for component in path.components() {
            match component {
                std::path::Component::CurDir => {}
                std::path::Component::Normal(part) => resolved.push(part),
                _ => return None,
            }
        }
        Some(resolved)
    } else {
        None
    }
}

fn service_keys_for_manifest(
    input: &PluginEnsureInput,
    manifest: &PluginManifest,
) -> Result<BTreeMap<String, PluginServiceKey>, PluginError> {
    if manifest.services.is_empty() {
        return Ok(BTreeMap::new());
    }
    let layer_stack_root =
        require_input_string(input.layer_stack_root.as_deref(), "layer_stack_root")?;
    let workspace_root = require_input_string(input.workspace_root.as_deref(), "workspace_root")?;
    manifest
        .services
        .iter()
        .map(|service| {
            let key = PluginServiceKey::new(PluginServiceKeyParts {
                layer_stack_root: layer_stack_root.clone(),
                workspace_root: workspace_root.clone(),
                plugin_id: manifest.plugin_id.clone(),
                plugin_digest: manifest.plugin_digest.clone(),
                service_id: service.service_id.clone(),
                service_profile_digest: service.service_profile_digest.clone(),
                service_mode: service.service_mode,
                refresh_strategy: service.refresh_strategy,
            })?;
            Ok((service.service_id.clone(), key))
        })
        .collect::<Result<BTreeMap<_, _>, PluginError>>()
}

fn require_input_string(value: Option<&str>, key: &str) -> Result<String, PluginError> {
    let Some(value) = value else {
        return Err(PluginError::Ensure(format!(
            "sandbox.plugin.ensure requires {key}"
        )));
    };
    Ok(value.to_owned())
}

fn validate_public_identifier(field: &str, value: &str) -> Result<(), PluginError> {
    if value.is_empty() {
        return Err(PluginError::Ensure(format!(
            "sandbox.plugin.ensure requires {field} name"
        )));
    }
    let mut chars = value.chars();
    match chars.next() {
        Some(c) if c == '_' || c.is_ascii_alphabetic() => {}
        _ => {
            return Err(PluginError::Ensure(format!(
                "{field} must start with an ASCII letter or underscore"
            )));
        }
    }
    if chars.all(|c| c == '_' || c.is_ascii_alphanumeric()) {
        Ok(())
    } else {
        Err(PluginError::Ensure(format!(
            "{field} contains unsupported characters"
        )))
    }
}

#[cfg(test)]
#[path = "../../tests/plugin/ensure_args.rs"]
mod tests;
