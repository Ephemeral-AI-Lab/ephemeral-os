//! Plugin setup/config helpers for the daemon facade.

use eos_plugin_runtime::PackageEnsureReport;
use eos_plugin::PluginManifest;
use serde_json::{json, Value};

use crate::error::DaemonError;

use super::service::stop_services_for_layer_stack_root as stop_services_for_layer_stack_root_in_state;
use super::state::{setup_failure_key, PluginRuntime};

impl PluginRuntime {
    /// PPC socket root for `ParsedEnsure` spec construction, from the typed
    /// runtime config.
    pub(super) fn ppc_socket_root(&self) -> String {
        self.config.ppc_root.to_string_lossy().into_owned()
    }

    pub(super) fn record_setup_failure(
        &self,
        manifest: Option<&PluginManifest>,
        err: &DaemonError,
    ) {
        let Some(manifest) = manifest else {
            return;
        };
        if let Ok(mut state) = self.lock_state() {
            state.setup_failures.insert(
                setup_failure_key(&manifest.plugin_id, &manifest.plugin_digest),
                json!({
                    "plugin": manifest.plugin_id,
                    "digest": manifest.plugin_digest,
                    "error": err.to_string(),
                }),
            );
        }
    }

    /// Stop and forget every connected service holding a snapshot on
    /// `layer_stack_root` (the workspace-base reset path).
    pub(crate) fn stop_services_for_layer_stack_root(
        &self,
        layer_stack_root: &str,
    ) -> Result<usize, DaemonError> {
        let mut state = self.lock_state()?;
        Ok(stop_services_for_layer_stack_root_in_state(
            &mut state,
            layer_stack_root,
        ))
    }
}

pub(super) fn package_report_value(report: &PackageEnsureReport) -> Value {
    if !report.active {
        return Value::Null;
    }
    json!({
        "needs_upload": report.needs_upload,
        "package_root": report.package_root.as_ref().map(|path| path.to_string_lossy().into_owned()),
        "dependency_root": report.dependency_root.as_ref().map(|path| path.to_string_lossy().into_owned()),
        "package_published": report.package_published,
        "setup_ran": report.setup_ran,
    })
}
