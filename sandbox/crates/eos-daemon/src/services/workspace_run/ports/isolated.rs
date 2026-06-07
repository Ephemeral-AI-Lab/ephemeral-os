use eos_isolated_workspace::command_session::types::{
    IsolatedCommandFinalizeContext, IsolatedCommandPrepareContext, IsolatedCommandSessionPort,
};
use eos_layerstack::LayerStack;
use eos_workspace_api::WorkspaceApiError;

use super::workspace_api_error;
use serde_json::Value;

use crate::response_timings::{resource_timings, timing_map};
use crate::services::workspace_run::isolated::CommandHandle;

pub(in crate::services::workspace_run) struct DaemonIsolatedCommandPort {
    handle: CommandHandle,
}

impl DaemonIsolatedCommandPort {
    pub(in crate::services::workspace_run) fn new(handle: CommandHandle) -> Self {
        Self { handle }
    }
}

impl IsolatedCommandSessionPort for DaemonIsolatedCommandPort {
    // The caller-keyed command-session registry now owns the isolated run's
    // sessions directly, so start/finish need no side-map bookkeeping here.
    fn command_session_started(&self, _command_session_id: &str, _caller_id: &str) {}

    fn command_session_finished(&self, _command_session_id: &str, _caller_id: &str, _status: &str) {}

    fn prepare_context(&self) -> Result<IsolatedCommandPrepareContext, WorkspaceApiError> {
        Ok(IsolatedCommandPrepareContext {
            workspace_handle_id: self.handle.workspace_handle_id.clone(),
            workspace_root: self.handle.workspace_root.clone(),
            scratch_dir: self.handle.scratch_dir.clone(),
            layer_paths: self.handle.layer_paths.clone(),
            upperdir: self.handle.upperdir.clone(),
            workdir: self.handle.workdir.clone(),
            ns_fds: self.handle.ns_fds.clone(),
            cgroup_path: self.handle.cgroup_path.clone(),
        })
    }

    fn finalize_context(&self) -> Result<IsolatedCommandFinalizeContext, WorkspaceApiError> {
        let manifest = LayerStack::open(self.handle.layer_stack_root.clone())
            .and_then(|stack| stack.read_active_manifest())
            .map_err(workspace_api_error)?;
        Ok(IsolatedCommandFinalizeContext {
            caller_id: self.handle.caller_id.clone(),
            workspace_handle_id: self.handle.workspace_handle_id.clone(),
            manifest_version: self.handle.manifest_version,
            manifest_root_hash: self.handle.manifest_root_hash.clone(),
            upperdir: self.handle.upperdir.clone(),
            base_timings: timing_map(resource_timings(&manifest, 0)),
        })
    }

    fn record_command_audit(&self, payload: Value) {
        crate::services::workspace_run::isolated::record_tool_call(&self.handle.caller_id, payload);
    }
}
