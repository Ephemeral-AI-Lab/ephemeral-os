use rusqlite::{params, Connection, OptionalExtension};

use super::rows::{
    ObservabilityNamespaceExecutionSnapshotRow, ObservabilityNamespaceExecutionTraceRow,
    ObservabilityRequestTraceRow, ObservabilityResourceSampleRow, ObservabilitySandboxSnapshotRow,
    ObservabilityWorkspaceSnapshotRow,
};
use super::{unix_time_ms, StoreError};

pub(super) fn read_sandbox_snapshot(
    connection: &Connection,
    sandbox_id: &str,
) -> Result<Option<ObservabilitySandboxSnapshotRow>, StoreError> {
    connection
        .query_row(
            "SELECT
                sandbox_id,
                state,
                daemon_runtime_dir,
                socket_path,
                pid_path,
                daemon_pid,
                sampled_at_unix_ms,
                error_message
             FROM sandbox_snapshots
             WHERE sandbox_id = ?1",
            [sandbox_id],
            sandbox_snapshot_from_row,
        )
        .optional()
        .map_err(StoreError::from)
}

pub(super) fn read_active_workspace_snapshots(
    connection: &Connection,
    sandbox_id: &str,
) -> Result<Vec<ObservabilityWorkspaceSnapshotRow>, StoreError> {
    let mut statement = connection.prepare(
        "SELECT
            workspace_id,
            state,
            remount_state,
            profile,
            namespace_fd_count,
            base_manifest_version,
            base_root_hash,
            layer_count,
            sampled_at_unix_ms,
            error_message
         FROM workspace_snapshots
         WHERE sandbox_id = ?1
           AND state != 'destroyed'
         ORDER BY workspace_id",
    )?;
    let rows = statement.query_map([sandbox_id], workspace_snapshot_from_row)?;
    rows.collect::<Result<Vec<_>, _>>()
        .map_err(StoreError::from)
}

pub(super) fn read_active_namespace_execution_snapshots(
    connection: &Connection,
    sandbox_id: &str,
) -> Result<Vec<ObservabilityNamespaceExecutionSnapshotRow>, StoreError> {
    let mut statement = connection.prepare(
        "SELECT
            namespace_execution_id,
            workspace_session_id,
            operation,
            lifecycle_state,
            sampled_at_unix_ms,
            error_message
         FROM namespace_execution_snapshots
         WHERE sandbox_id = ?1
         ORDER BY workspace_session_id, namespace_execution_id",
    )?;
    let rows = statement.query_map([sandbox_id], namespace_execution_snapshot_from_row)?;
    rows.collect::<Result<Vec<_>, _>>()
        .map_err(StoreError::from)
}

pub(super) fn read_latest_resource_samples(
    connection: &Connection,
    sandbox_id: &str,
    workspaces: &[ObservabilityWorkspaceSnapshotRow],
) -> Result<Vec<ObservabilityResourceSampleRow>, StoreError> {
    let mut latest = Vec::new();
    if let Some(sample) = read_latest_resource_sample(connection, sandbox_id, None)? {
        latest.push(sample);
    }
    for workspace in workspaces {
        if let Some(sample) =
            read_latest_resource_sample(connection, sandbox_id, Some(&workspace.workspace_id))?
        {
            latest.push(sample);
        }
    }
    Ok(latest)
}

pub(super) fn read_resource_history(
    connection: &Connection,
    sandbox_id: &str,
    window_ms: u64,
) -> Result<Vec<ObservabilityResourceSampleRow>, StoreError> {
    let cutoff = unix_time_ms().saturating_sub(i64::try_from(window_ms).unwrap_or(i64::MAX));
    let mut statement = connection.prepare(
        &(RESOURCE_SAMPLE_SUMMARY_SELECT_PREFIX.to_owned()
            + " WHERE sandbox_id = ?1
                  AND sampled_at_unix_ms >= ?2
                ORDER BY sampled_at_unix_ms DESC, workspace_id, sample_id"),
    )?;
    let rows = statement.query_map(
        params![sandbox_id, cutoff],
        resource_sample_summary_from_row,
    )?;
    rows.collect::<Result<Vec<_>, _>>()
        .map_err(StoreError::from)
}

pub(super) fn read_recent_request_traces(
    connection: &Connection,
    sandbox_id: &str,
    limit: usize,
) -> Result<Vec<ObservabilityRequestTraceRow>, StoreError> {
    let mut statement = connection.prepare(
        "SELECT
            trace_id,
            kind,
            status,
            operation,
            request_id,
            workspace_id,
            started_at_unix_ms,
            finished_at_unix_ms,
            duration_ms,
            error_kind,
            error_message
         FROM traces
         WHERE sandbox_id = ?1
         ORDER BY started_at_unix_ms DESC, trace_id
         LIMIT ?2",
    )?;
    let rows = statement.query_map(params![sandbox_id, limit_i64(limit)], trace_from_row)?;
    rows.collect::<Result<Vec<_>, _>>()
        .map_err(StoreError::from)
}

pub(super) fn read_recent_namespace_traces(
    connection: &Connection,
    sandbox_id: &str,
    limit: usize,
) -> Result<Vec<ObservabilityNamespaceExecutionTraceRow>, StoreError> {
    let mut statement = connection.prepare(
        "SELECT
            trace_id,
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
         ORDER BY started_at_unix_ms DESC, trace_id
         LIMIT ?2",
    )?;
    let rows = statement.query_map(
        params![sandbox_id, limit_i64(limit)],
        namespace_execution_trace_from_row,
    )?;
    rows.collect::<Result<Vec<_>, _>>()
        .map_err(StoreError::from)
}

fn read_latest_resource_sample(
    connection: &Connection,
    sandbox_id: &str,
    workspace_id: Option<&str>,
) -> Result<Option<ObservabilityResourceSampleRow>, StoreError> {
    match workspace_id {
        Some(workspace_id) => connection
            .query_row(
                &(RESOURCE_SAMPLE_SUMMARY_SELECT_PREFIX.to_owned()
                    + " WHERE sandbox_id = ?1
                          AND workspace_id = ?2
                        ORDER BY sampled_at_unix_ms DESC, sample_id DESC
                        LIMIT 1"),
                params![sandbox_id, workspace_id],
                resource_sample_summary_from_row,
            )
            .optional()
            .map_err(StoreError::from),
        None => connection
            .query_row(
                &(RESOURCE_SAMPLE_SUMMARY_SELECT_PREFIX.to_owned()
                    + " WHERE sandbox_id = ?1
                          AND workspace_id IS NULL
                        ORDER BY sampled_at_unix_ms DESC, sample_id DESC
                        LIMIT 1"),
                [sandbox_id],
                resource_sample_summary_from_row,
            )
            .optional()
            .map_err(StoreError::from),
    }
}

const RESOURCE_SAMPLE_SUMMARY_SELECT_PREFIX: &str = "SELECT
    workspace_id,
    sampled_at_unix_ms,
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
 FROM resource_samples";

fn sandbox_snapshot_from_row(
    row: &rusqlite::Row<'_>,
) -> rusqlite::Result<ObservabilitySandboxSnapshotRow> {
    Ok(ObservabilitySandboxSnapshotRow {
        sandbox_id: row.get(0)?,
        state: row.get(1)?,
        daemon_runtime_dir: row.get(2)?,
        socket_path: row.get(3)?,
        pid_path: row.get(4)?,
        daemon_pid: row.get(5)?,
        sampled_at_unix_ms: row.get(6)?,
        error_message: row.get(7)?,
    })
}

fn workspace_snapshot_from_row(
    row: &rusqlite::Row<'_>,
) -> rusqlite::Result<ObservabilityWorkspaceSnapshotRow> {
    Ok(ObservabilityWorkspaceSnapshotRow {
        workspace_id: row.get(0)?,
        state: row.get(1)?,
        remount_state: row.get(2)?,
        profile: row.get(3)?,
        namespace_fd_count: row.get(4)?,
        base_manifest_version: row.get(5)?,
        base_root_hash: row.get(6)?,
        layer_count: row.get(7)?,
        sampled_at_unix_ms: row.get(8)?,
        error_message: row.get(9)?,
    })
}

fn namespace_execution_snapshot_from_row(
    row: &rusqlite::Row<'_>,
) -> rusqlite::Result<ObservabilityNamespaceExecutionSnapshotRow> {
    Ok(ObservabilityNamespaceExecutionSnapshotRow {
        namespace_execution_id: row.get(0)?,
        workspace_session_id: row.get(1)?,
        operation: row.get(2)?,
        lifecycle_state: row.get(3)?,
        sampled_at_unix_ms: row.get(4)?,
        error_message: row.get(5)?,
    })
}

fn trace_from_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<ObservabilityRequestTraceRow> {
    Ok(ObservabilityRequestTraceRow {
        trace_id: row.get(0)?,
        kind: row.get(1)?,
        status: row.get(2)?,
        operation: row.get(3)?,
        request_id: row.get(4)?,
        workspace_id: row.get(5)?,
        started_at_unix_ms: row.get(6)?,
        finished_at_unix_ms: row.get(7)?,
        duration_ms: row.get(8)?,
        error_kind: row.get(9)?,
        error_message: row.get(10)?,
    })
}

fn namespace_execution_trace_from_row(
    row: &rusqlite::Row<'_>,
) -> rusqlite::Result<ObservabilityNamespaceExecutionTraceRow> {
    Ok(ObservabilityNamespaceExecutionTraceRow {
        trace_id: row.get(0)?,
        namespace_execution_id: row.get(1)?,
        workspace_session_id: row.get(2)?,
        operation: row.get(3)?,
        request_id: row.get(4)?,
        status: row.get(5)?,
        exit_code: row.get(6)?,
        started_at_unix_ms: row.get(7)?,
        finished_at_unix_ms: row.get(8)?,
        duration_ms: row.get(9)?,
        error_kind: row.get(10)?,
        error_message: row.get(11)?,
    })
}

fn resource_sample_summary_from_row(
    row: &rusqlite::Row<'_>,
) -> rusqlite::Result<ObservabilityResourceSampleRow> {
    Ok(ObservabilityResourceSampleRow {
        workspace_id: row.get(0)?,
        sampled_at_unix_ms: row.get(1)?,
        cgroup_available: row.get::<_, i64>(2)? != 0,
        cgroup_error: row.get(3)?,
        cpu_usage_usec: row.get(4)?,
        memory_current_bytes: row.get(5)?,
        memory_max_bytes: row.get(6)?,
        memory_max_unlimited: row.get::<_, Option<i64>>(7)?.map(|value| value != 0),
        disk_upperdir_bytes: row.get(8)?,
        disk_file_count: row.get(9)?,
        disk_dir_count: row.get(10)?,
        disk_symlink_count: row.get(11)?,
        disk_truncated: row.get::<_, Option<i64>>(12)?.map(|value| value != 0),
        disk_read_error_count: row.get(13)?,
        disk_first_error_path: row.get(14)?,
    })
}

fn limit_i64(limit: usize) -> i64 {
    i64::try_from(limit).unwrap_or(i64::MAX)
}
