use std::sync::{Mutex, MutexGuard, PoisonError};

use eos_isolated_workspace::command_session::types::{
    IsolatedCommandFinalizeContext, IsolatedCommandPrepareContext, IsolatedCommandSessionPort,
};
use eos_isolated_workspace::IsolatedWorkspaceOps;
use eos_layerstack::LayerStack;
use eos_workspace_api::{
    CommandWorkspacePolicy, FinalizeCommandRequest, PrepareCommandRequest,
    PreparedCommandWorkspace, WorkspaceApiError, WorkspaceCommandOutcome,
};
use serde_json::Value;

use crate::response_timings::{resource_timings, timing_map};
use crate::services::isolated_workspace::CommandHandle;

pub(crate) struct IsolatedCommandPolicy {
    handle: CommandHandle,
    state: Mutex<Option<IsolatedCommandWorkspace>>,
}

pub(crate) struct IsolatedCommandWorkspace {
    pub(crate) handle: CommandHandle,
}

pub(crate) struct IsolatedCommandPreparePort {
    handle: CommandHandle,
}

impl IsolatedCommandPolicy {
    pub(crate) fn new(handle: CommandHandle) -> Self {
        Self {
            handle,
            state: Mutex::new(None),
        }
    }
}

impl CommandWorkspacePolicy for IsolatedCommandPolicy {
    fn prepare_command_workspace(
        &self,
        request: PrepareCommandRequest,
    ) -> Result<PreparedCommandWorkspace, WorkspaceApiError> {
        let prepared =
            IsolatedWorkspaceOps::new(IsolatedCommandPreparePort::new(self.handle.clone()))
                .prepare_command_workspace(request.clone())?;
        std::fs::write(
            prepared.session_dir.join("metadata.json"),
            serde_json::to_vec_pretty(&serde_json::json!({
                "command_session_id": request.command_session_id,
                "caller_id": self.handle.caller_id,
                "invocation_id": request.invocation_id,
                "workspace": "isolated",
                "workspace_handle_id": self.handle.workspace_handle_id,
                "command": request.cmd,
                "status": "running",
            }))
            .map_err(workspace_api_error)?,
        )
        .map_err(workspace_api_error)?;
        *lock(&self.state) = Some(IsolatedCommandWorkspace {
            handle: self.handle.clone(),
        });
        Ok(prepared)
    }

    fn finalize_command_workspace(
        &self,
        request: FinalizeCommandRequest,
    ) -> Result<WorkspaceCommandOutcome, WorkspaceApiError> {
        let workspace = lock(&self.state).take().ok_or_else(|| {
            WorkspaceApiError::new(
                "daemon_command_workspace_error",
                "isolated command workspace is not prepared",
            )
        })?;
        let mut outcome = IsolatedWorkspaceOps::new(IsolatedCommandFinalizePort {
            workspace: &workspace,
        })
        .finalize_command_workspace(request)?;
        let audit = outcome
            .metadata
            .get("audit")
            .cloned()
            .unwrap_or_else(|| serde_json::json!({}));
        if let Some(metadata) = outcome.metadata.as_object_mut() {
            metadata.remove("audit");
        }
        crate::services::isolated_workspace::record_tool_call(
            &workspace.handle.caller_id,
            merge_audit_changed_paths(audit, serde_json::json!(outcome.changed_paths)),
        );
        Ok(outcome)
    }
}

impl IsolatedCommandPreparePort {
    pub(crate) fn new(handle: CommandHandle) -> Self {
        Self { handle }
    }
}

impl IsolatedCommandSessionPort for IsolatedCommandPreparePort {
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
}

pub(crate) struct IsolatedCommandFinalizePort<'a> {
    pub(crate) workspace: &'a IsolatedCommandWorkspace,
}

impl IsolatedCommandSessionPort for IsolatedCommandFinalizePort<'_> {
    fn finalize_context(&self) -> Result<IsolatedCommandFinalizeContext, WorkspaceApiError> {
        let manifest = LayerStack::open(self.workspace.handle.layer_stack_root.clone())
            .and_then(|stack| stack.read_active_manifest())
            .map_err(workspace_api_error)?;
        Ok(IsolatedCommandFinalizeContext {
            caller_id: self.workspace.handle.caller_id.clone(),
            workspace_handle_id: self.workspace.handle.workspace_handle_id.clone(),
            manifest_version: self.workspace.handle.manifest_version,
            manifest_root_hash: self.workspace.handle.manifest_root_hash.clone(),
            upperdir: self.workspace.handle.upperdir.clone(),
            base_timings: timing_map(resource_timings(&manifest, 0)),
        })
    }
}

fn workspace_api_error(error: impl std::fmt::Display) -> WorkspaceApiError {
    WorkspaceApiError::new("daemon_command_workspace_error", error.to_string())
}

fn merge_audit_changed_paths(mut audit: Value, changed_paths: Value) -> Value {
    if let Some(object) = audit.as_object_mut() {
        object.insert("changed_paths".to_owned(), changed_paths);
    }
    audit
}

fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(PoisonError::into_inner)
}
