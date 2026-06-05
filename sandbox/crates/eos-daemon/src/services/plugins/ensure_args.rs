use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use eos_plugin::{
    public_op_name, PluginError, PluginManifest, PluginServiceKey, PluginServiceKeyParts,
    PluginServiceManifest, PluginServiceState, PluginServiceStatus, ServiceMode,
};
use serde_json::Value;

use super::{
    package::{self, PackageRoots},
    plugin_runtime_config,
    process::PluginProcessSpec,
    state::{LoadedPluginRuntime, PluginOperationRoute, MAX_PLUGIN_CALLER_FIELD_CHARS},
};
use crate::error::DaemonError;

pub(super) fn validate_plugin_caller_fields(args: &Value) -> Result<(), DaemonError> {
    const TOP_LEVEL_FIELDS: &[&str] = &["caller_id", "invocation_id"];

    for field in TOP_LEVEL_FIELDS {
        validate_plugin_audit_field(field, args.get(*field))?;
    }
    if let Some(caller) = args.get("caller").and_then(Value::as_object) {
        for (field, value) in caller {
            validate_plugin_audit_field(field, Some(value))?;
        }
    }
    Ok(())
}

fn validate_plugin_audit_field(field: &str, value: Option<&Value>) -> Result<(), DaemonError> {
    let Some(value) = value else {
        return Ok(());
    };
    let Some(text) = value.as_str() else {
        return Err(DaemonError::Plugin(PluginError::Ppc(format!(
            "plugin caller field {field} must be a string"
        ))));
    };
    if text.contains('\0') {
        return Err(DaemonError::Plugin(PluginError::Ppc(format!(
            "plugin caller field {field} contains NUL"
        ))));
    }
    if text.chars().count() > MAX_PLUGIN_CALLER_FIELD_CHARS {
        return Err(DaemonError::Plugin(PluginError::Ppc(format!(
            "plugin caller field {field} exceeds {MAX_PLUGIN_CALLER_FIELD_CHARS} characters"
        ))));
    }
    Ok(())
}

pub(super) struct ParsedEnsure {
    pub(super) plugin_id: String,
    pub(super) plugin_digest: String,
    pub(super) manifest: Option<PluginManifest>,
    pub(super) registered_ops: Vec<String>,
    pub(super) operation_routes: BTreeMap<String, PluginOperationRoute>,
    pub(super) services: Vec<PluginServiceStatus>,
    pub(super) service_processes: Vec<PluginProcessSpec>,
    pub(super) runtime_loaded: bool,
}

pub(super) fn loaded_matches_parsed(loaded: &LoadedPluginRuntime, parsed: &ParsedEnsure) -> bool {
    loaded.digest == parsed.plugin_digest
        && loaded.registered_ops == parsed.registered_ops
        && loaded.operation_routes == parsed.operation_routes
        && loaded.service_processes == parsed.service_processes
        && loaded.runtime_loaded == parsed.runtime_loaded
}

impl ParsedEnsure {
    pub(super) fn from_args(args: &Value) -> Result<Self, DaemonError> {
        if let Some(manifest_value) = args.get("manifest") {
            let manifest: PluginManifest = serde_json::from_value(manifest_value.clone())
                .map_err(|err| PluginError::Manifest(err.to_string()))?;
            manifest.validate()?;
            return Self::from_manifest(args, manifest);
        }

        let plugin_id = args
            .get("plugin")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .trim()
            .to_owned();
        validate_public_identifier("plugin", &plugin_id)?;
        let plugin_digest = args
            .get("digest")
            .and_then(Value::as_str)
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

    fn from_manifest(args: &Value, manifest: PluginManifest) -> Result<Self, DaemonError> {
        let manifest_for_package = manifest.clone();
        let ppc_socket_root = ppc_socket_root(args);
        let package_roots = package::package_roots(args, &manifest)?;
        let layer_stack_root = args
            .get("layer_stack_root")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|root| !root.is_empty())
            .map(str::to_owned);
        let service_keys = service_keys_for_manifest(args, &manifest)?;
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
            &ppc_socket_root,
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
        ServiceMode::WorkspaceSnapshotRefresh => {
            "process-backed PPC execution is not started".to_owned()
        }
        ServiceMode::OneshotOverlay => "oneshot overlay worker starts per operation".to_owned(),
        _ => "unsupported plugin service mode".to_owned(),
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
    args: &Value,
    manifest: &PluginManifest,
) -> Result<BTreeMap<String, PluginServiceKey>, DaemonError> {
    if manifest.services.is_empty() {
        return Ok(BTreeMap::new());
    }
    let layer_stack_root = require_string(args, "layer_stack_root")?;
    let workspace_root = require_string(args, "workspace_root")?;
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
        .map_err(DaemonError::from)
}

fn ppc_socket_root(args: &Value) -> String {
    #[cfg(test)]
    {
        if let Some(root) = args.get("ppc_socket_root").and_then(Value::as_str) {
            return root.to_owned();
        }
    }
    let _ = args;
    plugin_runtime_config()
        .ppc_root
        .to_string_lossy()
        .into_owned()
}

fn require_string(args: &Value, key: &str) -> Result<String, DaemonError> {
    let value = args
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or_default()
        .trim()
        .to_owned();
    if value.is_empty() {
        return Err(DaemonError::Plugin(PluginError::Ensure(format!(
            "api.plugin.ensure requires {key}"
        ))));
    }
    Ok(value)
}

fn validate_public_identifier(field: &str, value: &str) -> Result<(), DaemonError> {
    if value.is_empty() {
        return Err(DaemonError::Plugin(PluginError::Ensure(format!(
            "api.plugin.ensure requires {field} name"
        ))));
    }
    let mut chars = value.chars();
    match chars.next() {
        Some(c) if c == '_' || c.is_ascii_alphabetic() => {}
        _ => {
            return Err(DaemonError::Plugin(PluginError::Ensure(format!(
                "{field} must start with an ASCII letter or underscore"
            ))));
        }
    }
    if chars.all(|c| c == '_' || c.is_ascii_alphanumeric()) {
        Ok(())
    } else {
        Err(DaemonError::Plugin(PluginError::Ensure(format!(
            "{field} contains unsupported characters"
        ))))
    }
}
