use sandbox_observability::{
    NamespaceExecutionSnapshotRecord, MAX_OPERATION_LENGTH, MAX_SNAPSHOT_STATE_LENGTH,
};
use sandbox_runtime::RuntimeNamespaceExecutionSnapshot;

pub(crate) fn snapshot_record(
    sandbox_id: &str,
    execution: &RuntimeNamespaceExecutionSnapshot,
    namespace_execution_id: String,
    workspace_session_id: String,
    sampled_at_unix_ms: i64,
) -> NamespaceExecutionSnapshotRecord {
    NamespaceExecutionSnapshotRecord {
        sandbox_id: sandbox_id.to_owned(),
        namespace_execution_id,
        workspace_session_id,
        operation: bound_operation(execution.operation_name.clone()),
        lifecycle_state: bound_state("running".to_owned()),
        sampled_at_unix_ms,
        error_message: None,
    }
}

fn bound_operation(value: String) -> String {
    bound_string(value, MAX_OPERATION_LENGTH)
}

fn bound_state(value: String) -> String {
    bound_string(value, MAX_SNAPSHOT_STATE_LENGTH)
}

fn bound_string(value: String, max_bytes: usize) -> String {
    if value.len() <= max_bytes {
        value
    } else {
        let mut end = max_bytes;
        while !value.is_char_boundary(end) {
            end = end.saturating_sub(1);
        }
        value[..end].to_owned()
    }
}
