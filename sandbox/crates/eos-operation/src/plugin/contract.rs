use std::collections::BTreeMap;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::core::request::{ArgProblem, ArgsError};
use crate::CallerId;

pub const MAX_PLUGIN_CALLER_FIELD_CHARS: usize = 256;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PluginEnsureInput {
    pub plugin: Option<String>,
    pub digest: Option<String>,
    pub manifest: Option<Value>,
    pub layer_stack_root: Option<String>,
    pub workspace_root: Option<String>,
    pub package: PluginPackageInput,
    pub start_services: bool,
    pub caller: CallerId,
    pub audit: PluginAuditFields,
}

impl PluginEnsureInput {
    pub(crate) fn parse(args: &Value) -> Result<Self, ArgsError> {
        Ok(Self {
            plugin: args
                .get("plugin")
                .and_then(Value::as_str)
                .map(str::to_owned),
            digest: args
                .get("digest")
                .and_then(Value::as_str)
                .map(str::to_owned),
            manifest: args.get("manifest").cloned(),
            start_services: args
                .get("start_services")
                .and_then(Value::as_bool)
                .unwrap_or(false),
            layer_stack_root: optional_trimmed_string(args, "layer_stack_root"),
            workspace_root: optional_trimmed_string(args, "workspace_root"),
            package: PluginPackageInput::parse(args),
            caller: CallerId::from_wire(args),
            audit: PluginAuditFields::parse(args)?,
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PluginPackageInput {
    pub package_runtime_root: Option<PathBuf>,
    pub package_dependency_root: Option<PathBuf>,
    pub package_upload_root: Option<PathBuf>,
    pub package_setup_root: Option<PathBuf>,
    pub staged_package_root: Option<String>,
    pub staged_package_root_present: bool,
}

impl PluginPackageInput {
    fn parse(args: &Value) -> Self {
        Self {
            package_runtime_root: optional_untrimmed_path(args, "package_runtime_root"),
            package_dependency_root: optional_untrimmed_path(args, "package_dependency_root"),
            package_upload_root: optional_untrimmed_path(args, "package_upload_root"),
            package_setup_root: optional_untrimmed_path(args, "package_setup_root"),
            staged_package_root: args
                .get("staged_package_root")
                .and_then(Value::as_str)
                .map(str::to_owned),
            staged_package_root_present: args.get("staged_package_root").is_some(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PluginAuditFields {
    pub invocation_id: Option<String>,
    pub caller: BTreeMap<String, String>,
}

impl PluginAuditFields {
    pub(crate) fn parse(args: &Value) -> Result<Self, ArgsError> {
        validate_plugin_audit_field(args, "caller_id")?;
        Ok(Self {
            invocation_id: optional_plugin_audit_field(args, "invocation_id")?,
            caller: caller_object_fields(args)?,
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PluginStatusInput {
    pub probe_services: bool,
    pub probe_timeout_ms: Option<u64>,
    pub caller: CallerId,
    pub audit: PluginAuditFields,
}

impl PluginStatusInput {
    pub(crate) fn parse(args: &Value) -> Result<Self, ArgsError> {
        Ok(Self {
            probe_services: args
                .get("probe_services")
                .and_then(Value::as_bool)
                .unwrap_or(false),
            probe_timeout_ms: args.get("probe_timeout_ms").and_then(Value::as_u64),
            caller: CallerId::from_wire(args),
            audit: PluginAuditFields::parse(args)?,
        })
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PluginNeedsUploadOutput {
    pub success: bool,
    pub plugin: String,
    pub digest: String,
    pub ready: bool,
    pub needs_upload: bool,
    pub runtime_loaded: bool,
    pub package_root: Option<PathBuf>,
    pub dependency_root: Option<PathBuf>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PluginEnsureReadyOutput {
    pub success: bool,
    pub plugin: String,
    pub digest: String,
    pub registered_ops: Vec<String>,
    pub runtime_loaded: bool,
    pub runtime_warmed: bool,
    pub service_processes_started: bool,
    pub started_service_process_count: usize,
    pub already_loaded: bool,
    pub operation_routes: Vec<Value>,
    pub services: Value,
    pub service_processes: Vec<Value>,
    pub running_service_processes: Value,
    pub connected_ppc_routes: Vec<String>,
    pub connected_ppc_services: Vec<String>,
    pub package: Value,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LoadedPluginStatusOutput {
    pub name: String,
    pub digest: String,
    pub ops: Vec<String>,
    pub operation_routes: Vec<Value>,
    pub services: Value,
    pub service_processes: Vec<Value>,
    pub runtime_loaded: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PluginStatusOutput {
    pub success: bool,
    pub loaded_plugins: Vec<LoadedPluginStatusOutput>,
    pub running_service_processes: Value,
    pub connected_ppc_routes: Vec<String>,
    pub connected_ppc_services: Vec<String>,
    pub setup_failures: Value,
    pub service_health: Vec<Value>,
    pub pending: Vec<Value>,
}

fn optional_trimmed_string(args: &Value, key: &str) -> Option<String> {
    args.get(key)
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
}

fn optional_untrimmed_path(args: &Value, key: &str) -> Option<PathBuf> {
    args.get(key)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
}

fn optional_plugin_audit_field(
    args: &Value,
    key: &'static str,
) -> Result<Option<String>, ArgsError> {
    validate_plugin_audit_field(args, key)?;
    Ok(args.get(key).and_then(Value::as_str).map(str::to_owned))
}

fn caller_object_fields(args: &Value) -> Result<BTreeMap<String, String>, ArgsError> {
    let Some(caller) = args.get("caller").and_then(Value::as_object) else {
        return Ok(BTreeMap::new());
    };
    caller
        .iter()
        .map(|(field, value)| {
            validate_plugin_audit_value(field, Some(value))?;
            Ok((field.clone(), value.as_str().unwrap_or_default().to_owned()))
        })
        .collect()
}

fn validate_plugin_audit_field(args: &Value, key: &'static str) -> Result<(), ArgsError> {
    validate_plugin_audit_value(key, args.get(key))
}

fn validate_plugin_audit_value(field: &str, value: Option<&Value>) -> Result<(), ArgsError> {
    let Some(value) = value else {
        return Ok(());
    };
    let Some(text) = value.as_str() else {
        return Err(plugin_audit_error(format!(
            "plugin caller field {field} must be a string"
        )));
    };
    if text.contains('\0') {
        return Err(plugin_audit_error(format!(
            "plugin caller field {field} contains NUL"
        )));
    }
    if text.chars().count() > MAX_PLUGIN_CALLER_FIELD_CHARS {
        return Err(plugin_audit_error(format!(
            "plugin caller field {field} exceeds {MAX_PLUGIN_CALLER_FIELD_CHARS} characters"
        )));
    }
    Ok(())
}

fn plugin_audit_error(message: String) -> ArgsError {
    ArgsError {
        key: "caller",
        problem: ArgProblem::Invalid(message),
    }
}
