//! Daemon-owned services shared by dispatch handlers.

use std::sync::Arc;
use std::time::Duration;

use command::CommandConfig;
use config::configs::daemon::{CommandConfig as ConfigCommandConfig, PluginRuntimeConfig};
use config::configs::isolated_network::IsolatedNetworkConfig;
use layerstack::service::{BoundedCaptureOptions, IgnoredCaptureLimits};
use layerstack::CommitOptions;
use operation_service::command::CommandFinalizationOptions;
use operation_service::workspace_manager::WorkspaceManagerService;
use operation_service::workspace_remount::{WorkspaceRemountOptions, WorkspaceRemountService};
use operation_service::{CommandOperationService, OperationServices};
use plugin::{PluginRuntime, PluginRuntimeError};
use serde_json::Value;
use workspace::{
    CaptureChangesRequest, CapturedWorkspaceChanges, CreateWorkspaceRequest,
    DestroyWorkspaceRequest, DestroyWorkspaceResult, LatestSnapshotRequest, ReadonlySnapshotHandle,
    RemountWorkspaceRequest, RemountWorkspaceResult, WorkspaceError, WorkspaceHandle,
    WorkspaceService,
};

#[must_use]
pub(crate) fn command_config_from_schema(config: &ConfigCommandConfig) -> CommandConfig {
    CommandConfig {
        scratch_root: config.scratch_root.clone(),
        default_yield_time_ms: config.default_yield_time_ms,
        default_timeout_s: config.default_timeout_s,
        quiet_ms: config.quiet_ms,
        cancel_wait_ms: config.cancel_wait_ms,
        output_drain_grace_ms: config.output_drain_grace_ms,
        max_command_s: config.max_command_s,
        transcript_timestamp_timezone: config.transcript_timestamp_timezone.clone(),
    }
}

#[must_use]
pub(crate) fn capture_options_from_schema(config: &ConfigCommandConfig) -> BoundedCaptureOptions {
    let limits = config.ignored_capture;
    BoundedCaptureOptions {
        materialize_payloads: true,
        ignored_limits: IgnoredCaptureLimits {
            max_ignored_files: limits.max_files,
            max_ignored_bytes: limits.max_bytes,
            max_ignored_file_bytes: limits.max_file_bytes,
            spool_threshold_bytes: limits.spool_threshold_bytes,
            max_metadata_capture_duration: Duration::from_millis(
                limits.max_metadata_capture_duration_ms,
            ),
        },
    }
}

/// Runtime service instances shared by daemon dispatch handlers.
pub struct RuntimeServices {
    pub operation: OperationServices,
    pub commit_options: CommitOptions,
    pub plugin: PluginRuntime,
}

impl RuntimeServices {
    #[must_use]
    pub fn new(
        plugin: PluginRuntimeConfig,
        isolated_network: IsolatedNetworkConfig,
        command: CommandConfig,
    ) -> Self {
        Self::with_commit_options_and_capture_options(
            plugin,
            isolated_network,
            command,
            CommitOptions::default(),
            BoundedCaptureOptions::default(),
        )
    }

    #[must_use]
    pub fn with_commit_options_and_capture_options(
        plugin: PluginRuntimeConfig,
        _isolated_network: IsolatedNetworkConfig,
        command: CommandConfig,
        commit_options: CommitOptions,
        capture_options: BoundedCaptureOptions,
    ) -> Self {
        let workspace = Arc::new(WorkspaceManagerService::new(Arc::new(
            UnsupportedWorkspaceService,
        )));
        let command = Arc::new(CommandOperationService::with_finalization_options(
            Arc::clone(&workspace),
            command,
            CommandFinalizationOptions {
                one_shot_capture: capture_options,
                one_shot_publish: commit_options,
            },
        ));
        let remount = Arc::new(WorkspaceRemountService::new(
            Arc::clone(&workspace),
            Arc::clone(&command),
            WorkspaceRemountOptions::default(),
        ));
        Self {
            operation: OperationServices::new(workspace, command, remount),
            commit_options,
            plugin: PluginRuntime::new(plugin),
        }
    }

    pub fn ensure_plugin_family_allowed(&self, args: &Value) -> Result<(), PluginRuntimeError> {
        plugin_contract::validate_plugin_caller_fields(args)
            .map_err(|err| PluginRuntimeError::InvalidRequest(err.message()))?;
        self.ensure_plugin_caller_allowed(plugin_contract::CallerId::from_wire(args).as_str())
    }

    pub fn ensure_plugin_caller_allowed(&self, caller: &str) -> Result<(), PluginRuntimeError> {
        let _ = caller;
        Ok(())
    }
}

struct UnsupportedWorkspaceService;

impl WorkspaceService for UnsupportedWorkspaceService {
    fn create_workspace(
        &self,
        _request: CreateWorkspaceRequest,
    ) -> Result<WorkspaceHandle, WorkspaceError> {
        Err(WorkspaceError::FeatureDisabled)
    }

    fn capture_changes(
        &self,
        _handle: &WorkspaceHandle,
        _request: CaptureChangesRequest,
    ) -> Result<CapturedWorkspaceChanges, WorkspaceError> {
        Err(WorkspaceError::FeatureDisabled)
    }

    fn remount_workspace(
        &self,
        _handle: &WorkspaceHandle,
        _request: RemountWorkspaceRequest,
    ) -> Result<RemountWorkspaceResult, WorkspaceError> {
        Err(WorkspaceError::FeatureDisabled)
    }

    fn destroy_workspace(
        &self,
        _handle: WorkspaceHandle,
        _request: DestroyWorkspaceRequest,
    ) -> Result<DestroyWorkspaceResult, WorkspaceError> {
        Err(WorkspaceError::FeatureDisabled)
    }

    fn latest_snapshot(
        &self,
        _request: LatestSnapshotRequest,
    ) -> Result<ReadonlySnapshotHandle, WorkspaceError> {
        Err(WorkspaceError::FeatureDisabled)
    }
}
