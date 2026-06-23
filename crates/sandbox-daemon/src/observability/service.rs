use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use sandbox_observability::{
    ExecutionSnapshotRecord, ObservabilityPaths, ObservabilityStore, ResourceSampleRecord,
    SandboxSnapshotRecord, StoreError, WorkspaceSnapshotRecord,
};
use sandbox_runtime::{
    RuntimeExecutionSnapshot, RuntimeObservabilitySnapshot, RuntimeWorkspaceSnapshot,
    SandboxRuntimeOperations,
};

use crate::server::ServerConfig;

use super::cgroup::CgroupSample;
use super::disk::{self, DiskSample};

const MAX_ERROR_LEN: usize = 4096;
const MAX_PATH_LEN: usize = 4096;

pub(crate) struct DaemonObservability {
    sandbox_id: String,
    paths: ObservabilityPaths,
    store: ObservabilityStore,
    next_sample_id: AtomicU64,
}

impl DaemonObservability {
    pub(crate) fn from_config(config: &ServerConfig) -> Option<Self> {
        let sandbox_id = config
            .sandbox_id
            .as_ref()
            .filter(|sandbox_id| !sandbox_id.is_empty())?
            .clone();
        let paths = ObservabilityPaths::from_socket_path(config.socket_path.clone()).ok()?;
        let store = ObservabilityStore::open(&paths).ok()?;
        Some(Self {
            sandbox_id,
            paths,
            store,
            next_sample_id: AtomicU64::new(1),
        })
    }

    pub(crate) fn collect(
        &self,
        config: &ServerConfig,
        operations: &SandboxRuntimeOperations,
    ) -> Result<(), StoreError> {
        self.write_snapshot(config, operations.observability_snapshot(), unix_ms())
    }

    #[cfg(test)]
    #[allow(dead_code, reason = "used by path-included daemon integration tests")]
    pub(crate) fn collect_runtime_snapshot_for_test(
        &self,
        config: &ServerConfig,
        snapshot: RuntimeObservabilitySnapshot,
    ) -> Result<(), StoreError> {
        self.write_snapshot(config, snapshot, unix_ms())
    }

    fn write_snapshot(
        &self,
        config: &ServerConfig,
        snapshot: RuntimeObservabilitySnapshot,
        sampled_at_unix_ms: i64,
    ) -> Result<(), StoreError> {
        self.store.upsert_sandbox_snapshot(&self.sandbox_record(
            config,
            sampled_at_unix_ms,
            &snapshot.partial_errors,
        ))?;

        let workspace_records = snapshot
            .workspaces
            .iter()
            .map(|workspace| self.workspace_record(workspace, sampled_at_unix_ms))
            .collect::<Vec<_>>();
        let active_workspace_ids = snapshot
            .workspaces
            .iter()
            .map(|workspace| workspace.workspace_id.0.clone())
            .collect::<Vec<_>>();
        self.store
            .upsert_workspace_snapshots(&self.sandbox_id, &workspace_records)?;
        self.store.reconcile_workspace_snapshots(
            &self.sandbox_id,
            &active_workspace_ids,
            sampled_at_unix_ms,
        )?;

        let execution_records = snapshot
            .active_executions
            .iter()
            .map(|execution| self.execution_record(execution, sampled_at_unix_ms))
            .collect::<Vec<_>>();
        let active_execution_ids = snapshot
            .active_executions
            .iter()
            .map(|execution| execution.execution_id.clone())
            .collect::<Vec<_>>();
        self.store
            .upsert_execution_snapshots(&self.sandbox_id, &execution_records)?;
        self.store
            .prune_execution_snapshots(&self.sandbox_id, &active_execution_ids)?;

        let resource_samples = self.resource_records(&snapshot, sampled_at_unix_ms);
        self.store.insert_resource_samples(&resource_samples)?;
        Ok(())
    }

    fn sandbox_record(
        &self,
        config: &ServerConfig,
        sampled_at_unix_ms: i64,
        partial_errors: &[String],
    ) -> SandboxSnapshotRecord {
        SandboxSnapshotRecord {
            sandbox_id: self.sandbox_id.clone(),
            state: if partial_errors.is_empty() {
                "ready".to_owned()
            } else {
                "unavailable".to_owned()
            },
            workspace_root: None,
            daemon_runtime_dir: Some(path_string(self.paths.daemon_runtime_dir())),
            socket_path: Some(path_string(&config.socket_path)),
            pid_path: Some(path_string(&config.pid_path)),
            daemon_pid: Some(i64::from(std::process::id())),
            sampled_at_unix_ms,
            error_message: error_summary(partial_errors),
        }
    }

    fn workspace_record(
        &self,
        workspace: &RuntimeWorkspaceSnapshot,
        sampled_at_unix_ms: i64,
    ) -> WorkspaceSnapshotRecord {
        WorkspaceSnapshotRecord {
            sandbox_id: self.sandbox_id.clone(),
            workspace_id: workspace.workspace_id.0.clone(),
            state: "active".to_owned(),
            remount_state: Some(workspace.remount_state.clone()),
            profile: Some(workspace.profile.as_str().to_owned()),
            workspace_root: Some(path_string(&workspace.workspace_root)),
            upperdir: workspace.upperdir.as_deref().map(path_string),
            workdir: workspace.workdir.as_deref().map(path_string),
            namespace_fd_count: workspace.namespace_fd_count.map(usize_to_i64),
            base_manifest_version: workspace.base_manifest_version,
            base_root_hash: workspace.base_root_hash.clone(),
            layer_count: workspace.layer_count.map(usize_to_i64),
            sampled_at_unix_ms,
            error_message: None,
        }
    }

    fn execution_record(
        &self,
        execution: &RuntimeExecutionSnapshot,
        sampled_at_unix_ms: i64,
    ) -> ExecutionSnapshotRecord {
        ExecutionSnapshotRecord {
            sandbox_id: self.sandbox_id.clone(),
            workspace_id: execution.workspace_id.0.clone(),
            execution_id: execution.execution_id.clone(),
            execution_kind: execution.execution_kind.clone(),
            operation: execution.operation.clone(),
            command_session_id: execution
                .command_session_id
                .as_ref()
                .map(|command_session_id| command_session_id.0.clone()),
            command: execution.command.clone(),
            lifecycle_state: execution.lifecycle_state.clone(),
            finalization_state: execution.finalization_state.clone(),
            workspace_ownership: Some(execution.workspace_ownership.clone()),
            started_at_unix_ms: execution.started_at_unix_ms,
            wall_time_ms: execution.wall_time_ms,
            process_group_id: execution.process_group_id.map(i64::from),
            transcript_path: execution.transcript_path.as_deref().map(path_string),
            sampled_at_unix_ms,
            error_message: None,
        }
    }

    fn resource_records(
        &self,
        snapshot: &RuntimeObservabilitySnapshot,
        sampled_at_unix_ms: i64,
    ) -> Vec<ResourceSampleRecord> {
        let mut records = vec![self.resource_record(
            None,
            sampled_at_unix_ms,
            CgroupSample::unavailable("cgroup path unavailable"),
            DiskSample::empty(),
        )];

        records.extend(snapshot.workspaces.iter().map(|workspace| {
            let disk = workspace
                .upperdir
                .as_deref()
                .map(disk::sample_upperdir)
                .unwrap_or_else(DiskSample::empty);
            self.resource_record(
                Some(workspace.workspace_id.0.as_str()),
                sampled_at_unix_ms,
                CgroupSample::unavailable("cgroup path unavailable"),
                disk,
            )
        }));
        records
    }

    fn resource_record(
        &self,
        workspace_id: Option<&str>,
        sampled_at_unix_ms: i64,
        cgroup: CgroupSample,
        disk: DiskSample,
    ) -> ResourceSampleRecord {
        ResourceSampleRecord {
            sample_id: self.next_sample_id(sampled_at_unix_ms),
            sandbox_id: self.sandbox_id.clone(),
            workspace_id: workspace_id.map(str::to_owned),
            sampled_at_unix_ms,
            cgroup_path: cgroup.cgroup_path.map(bound_path),
            cgroup_available: cgroup.cgroup_available,
            cgroup_error: cgroup.cgroup_error.map(bound_error),
            cpu_usage_usec: cgroup.cpu_usage_usec,
            memory_current_bytes: cgroup.memory_current_bytes,
            memory_max_bytes: cgroup.memory_max_bytes,
            memory_max_unlimited: cgroup.memory_max_unlimited,
            disk_upperdir_bytes: disk.upperdir_bytes,
            disk_file_count: disk.file_count,
            disk_dir_count: disk.dir_count,
            disk_symlink_count: disk.symlink_count,
            disk_truncated: disk.truncated,
            disk_read_error_count: disk.read_error_count,
            disk_first_error_path: disk.first_error_path.map(bound_path),
        }
    }

    fn next_sample_id(&self, sampled_at_unix_ms: i64) -> String {
        let next = self.next_sample_id.fetch_add(1, Ordering::Relaxed);
        format!("sample-{sampled_at_unix_ms}-{next}")
    }
}

fn unix_ms() -> i64 {
    let duration = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    i64::try_from(duration.as_millis()).unwrap_or(i64::MAX)
}

fn path_string(path: &Path) -> String {
    PathBuf::from(path).to_string_lossy().into_owned()
}

fn usize_to_i64(value: usize) -> i64 {
    i64::try_from(value).unwrap_or(i64::MAX)
}

fn error_summary(errors: &[String]) -> Option<String> {
    if errors.is_empty() {
        return None;
    }
    Some(bound_error(
        errors
            .iter()
            .map(String::as_str)
            .collect::<Vec<_>>()
            .join("; "),
    ))
}

fn bound_error(value: String) -> String {
    bound_string(value, MAX_ERROR_LEN)
}

fn bound_path(value: String) -> String {
    bound_string(value, MAX_PATH_LEN)
}

fn bound_string(value: String, max_chars: usize) -> String {
    if value.len() <= max_chars {
        value
    } else {
        value.chars().take(max_chars).collect()
    }
}
