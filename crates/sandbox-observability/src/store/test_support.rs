use rusqlite::OptionalExtension;

use crate::records::{
    NamespaceExecutionSnapshotRecord, NamespaceExecutionTraceRecord, ResourceSampleRecord,
    SandboxSnapshotRecord, SpanRecord, TraceRecord, WorkspaceSnapshotRecord,
};

use super::{ObservabilityStore, StoreError};

impl ObservabilityStore {
    #[doc(hidden)]
    pub fn force_sqlite_write_errors_for_test(&self) -> Result<(), StoreError> {
        let connection = self.connection()?;
        connection.pragma_update(None, "query_only", "ON")?;
        Ok(())
    }

    #[doc(hidden)]
    pub fn trace_for_test(&self, trace_id: &str) -> Result<Option<TraceRecord>, StoreError> {
        let connection = self.connection()?;
        connection
            .query_row(
                "SELECT
                    trace_id,
                    kind,
                    status,
                    sandbox_id,
                    operation,
                    request_id,
                    origin_request_id,
                    workspace_id,
                    command_session_id,
                    started_at_unix_ms,
                    finished_at_unix_ms,
                    duration_ms,
                    error_kind,
                    error_message
                 FROM traces
                 WHERE trace_id = ?1",
                [trace_id],
                |row| {
                    Ok(TraceRecord {
                        trace_id: row.get(0)?,
                        kind: row.get(1)?,
                        status: row.get(2)?,
                        sandbox_id: row.get(3)?,
                        operation: row.get(4)?,
                        request_id: row.get(5)?,
                        origin_request_id: row.get(6)?,
                        workspace_id: row.get(7)?,
                        command_session_id: row.get(8)?,
                        started_at_unix_ms: row.get(9)?,
                        finished_at_unix_ms: row.get(10)?,
                        duration_ms: row.get(11)?,
                        error_kind: row.get(12)?,
                        error_message: row.get(13)?,
                    })
                },
            )
            .optional()
            .map_err(StoreError::from)
    }

    #[doc(hidden)]
    pub fn spans_for_test(&self, trace_id: &str) -> Result<Vec<SpanRecord>, StoreError> {
        let connection = self.connection()?;
        let mut statement = connection.prepare(
            "SELECT
                span_id,
                trace_id,
                parent_span_id,
                method_name,
                call_index,
                status,
                started_at_unix_ms,
                finished_at_unix_ms,
                duration_ms,
                error_kind,
                error_message
             FROM spans
             WHERE trace_id = ?1
             ORDER BY call_index",
        )?;
        let rows = statement.query_map([trace_id], |row| {
            Ok(SpanRecord {
                span_id: row.get(0)?,
                trace_id: row.get(1)?,
                parent_span_id: row.get(2)?,
                method_name: row.get(3)?,
                call_index: row.get(4)?,
                status: row.get(5)?,
                started_at_unix_ms: row.get(6)?,
                finished_at_unix_ms: row.get(7)?,
                duration_ms: row.get(8)?,
                error_kind: row.get(9)?,
                error_message: row.get(10)?,
            })
        })?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(StoreError::from)
    }

    #[doc(hidden)]
    pub fn sandbox_snapshot_for_test(
        &self,
        sandbox_id: &str,
    ) -> Result<Option<SandboxSnapshotRecord>, StoreError> {
        let connection = self.connection()?;
        connection
            .query_row(
                "SELECT
                    sandbox_id,
                    state,
                    workspace_root,
                    daemon_runtime_dir,
                    socket_path,
                    pid_path,
                    daemon_pid,
                    sampled_at_unix_ms,
                    error_message
                FROM sandbox_snapshots
                WHERE sandbox_id = ?1",
                [sandbox_id],
                |row| {
                    Ok(SandboxSnapshotRecord {
                        sandbox_id: row.get(0)?,
                        state: row.get(1)?,
                        workspace_root: row.get(2)?,
                        daemon_runtime_dir: row.get(3)?,
                        socket_path: row.get(4)?,
                        pid_path: row.get(5)?,
                        daemon_pid: row.get(6)?,
                        sampled_at_unix_ms: row.get(7)?,
                        error_message: row.get(8)?,
                    })
                },
            )
            .optional()
            .map_err(StoreError::from)
    }

    #[doc(hidden)]
    pub fn workspace_snapshots_for_test(
        &self,
        sandbox_id: &str,
    ) -> Result<Vec<WorkspaceSnapshotRecord>, StoreError> {
        let connection = self.connection()?;
        let mut statement = connection.prepare(
            "SELECT
                sandbox_id,
                workspace_id,
                state,
                remount_state,
                profile,
                workspace_root,
                upperdir,
                workdir,
                namespace_fd_count,
                base_manifest_version,
                base_root_hash,
                layer_count,
                sampled_at_unix_ms,
                error_message
            FROM workspace_snapshots
            WHERE sandbox_id = ?1
            ORDER BY workspace_id",
        )?;
        let rows = statement.query_map([sandbox_id], |row| {
            Ok(WorkspaceSnapshotRecord {
                sandbox_id: row.get(0)?,
                workspace_id: row.get(1)?,
                state: row.get(2)?,
                remount_state: row.get(3)?,
                profile: row.get(4)?,
                workspace_root: row.get(5)?,
                upperdir: row.get(6)?,
                workdir: row.get(7)?,
                namespace_fd_count: row.get(8)?,
                base_manifest_version: row.get(9)?,
                base_root_hash: row.get(10)?,
                layer_count: row.get(11)?,
                sampled_at_unix_ms: row.get(12)?,
                error_message: row.get(13)?,
            })
        })?;
        rows.collect::<Result<_, _>>().map_err(StoreError::from)
    }

    #[doc(hidden)]
    pub fn namespace_execution_snapshots_for_test(
        &self,
        sandbox_id: &str,
    ) -> Result<Vec<NamespaceExecutionSnapshotRecord>, StoreError> {
        let connection = self.connection()?;
        let mut statement = connection.prepare(
            "SELECT
                sandbox_id,
                namespace_execution_id,
                workspace_session_id,
                operation,
                lifecycle_state,
                sampled_at_unix_ms,
                error_message
            FROM namespace_execution_snapshots
            WHERE sandbox_id = ?1
            ORDER BY namespace_execution_id",
        )?;
        let rows = statement.query_map([sandbox_id], |row| {
            Ok(NamespaceExecutionSnapshotRecord {
                sandbox_id: row.get(0)?,
                namespace_execution_id: row.get(1)?,
                workspace_session_id: row.get(2)?,
                operation: row.get(3)?,
                lifecycle_state: row.get(4)?,
                sampled_at_unix_ms: row.get(5)?,
                error_message: row.get(6)?,
            })
        })?;
        rows.collect::<Result<_, _>>().map_err(StoreError::from)
    }

    #[doc(hidden)]
    pub fn namespace_execution_traces_for_test(
        &self,
        sandbox_id: &str,
    ) -> Result<Vec<NamespaceExecutionTraceRecord>, StoreError> {
        let connection = self.connection()?;
        let mut statement = connection.prepare(
            "SELECT
                trace_id,
                sandbox_id,
                namespace_execution_id,
                workspace_session_id,
                operation,
                request_id,
                status,
                exit_code,
                started_at_unix_ms,
                finished_at_unix_ms,
                duration_ms,
                error_kind,
                error_message
            FROM namespace_execution_traces
            WHERE sandbox_id = ?1
            ORDER BY namespace_execution_id",
        )?;
        let rows = statement.query_map([sandbox_id], |row| {
            Ok(NamespaceExecutionTraceRecord {
                trace_id: row.get(0)?,
                sandbox_id: row.get(1)?,
                namespace_execution_id: row.get(2)?,
                workspace_session_id: row.get(3)?,
                operation: row.get(4)?,
                request_id: row.get(5)?,
                status: row.get(6)?,
                exit_code: row.get(7)?,
                started_at_unix_ms: row.get(8)?,
                finished_at_unix_ms: row.get(9)?,
                duration_ms: row.get(10)?,
                error_kind: row.get(11)?,
                error_message: row.get(12)?,
            })
        })?;
        rows.collect::<Result<_, _>>().map_err(StoreError::from)
    }

    #[doc(hidden)]
    pub fn resource_samples_for_test(
        &self,
        sandbox_id: &str,
    ) -> Result<Vec<ResourceSampleRecord>, StoreError> {
        let connection = self.connection()?;
        let mut statement = connection.prepare(
            "SELECT
                sample_id,
                sandbox_id,
                workspace_id,
                sampled_at_unix_ms,
                cgroup_path,
                cgroup_available,
                cgroup_error,
                cpu_usage_usec,
                memory_current_bytes,
                memory_max_bytes,
                memory_max_unlimited,
                disk_upperdir_bytes,
                disk_file_count,
                disk_dir_count,
                disk_symlink_count,
                disk_truncated,
                disk_read_error_count,
                disk_first_error_path
            FROM resource_samples
            WHERE sandbox_id = ?1
            ORDER BY sampled_at_unix_ms, sample_id",
        )?;
        let rows = statement.query_map([sandbox_id], |row| {
            Ok(ResourceSampleRecord {
                sample_id: row.get(0)?,
                sandbox_id: row.get(1)?,
                workspace_id: row.get(2)?,
                sampled_at_unix_ms: row.get(3)?,
                cgroup_path: row.get(4)?,
                cgroup_available: row.get::<_, i64>(5)? != 0,
                cgroup_error: row.get(6)?,
                cpu_usage_usec: row.get(7)?,
                memory_current_bytes: row.get(8)?,
                memory_max_bytes: row.get(9)?,
                memory_max_unlimited: row.get::<_, Option<i64>>(10)?.map(|value| value != 0),
                disk_upperdir_bytes: row.get(11)?,
                disk_file_count: row.get(12)?,
                disk_dir_count: row.get(13)?,
                disk_symlink_count: row.get(14)?,
                disk_truncated: row.get::<_, Option<i64>>(15)?.map(|value| value != 0),
                disk_read_error_count: row.get(16)?,
                disk_first_error_path: row.get(17)?,
            })
        })?;
        rows.collect::<Result<_, _>>().map_err(StoreError::from)
    }
}
