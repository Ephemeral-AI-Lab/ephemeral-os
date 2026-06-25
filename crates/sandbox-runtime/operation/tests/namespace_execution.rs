use sandbox_runtime::{
    NamespaceExecutionId, NamespaceExecutionLedger, NamespaceExecutionRecord,
    NamespaceExecutionTerminalStatus, WorkspaceSessionId,
};

fn ok_record(id: &str, workspace_session_id: &str) -> NamespaceExecutionRecord {
    NamespaceExecutionRecord {
        namespace_execution_id: NamespaceExecutionId(id.to_owned()),
        workspace_session_id: WorkspaceSessionId(workspace_session_id.to_owned()),
        operation_name: "exec_command".to_owned(),
        origin_request_id: None,
        started_at_unix_ms: 1_000,
        finished_at_unix_ms: Some(1_025),
        duration_ms: Some(25.0),
        terminal_status: Some(NamespaceExecutionTerminalStatus::Ok),
        exit_code: Some(0),
        error_kind: None,
        error_message: None,
    }
}

#[test]
fn record_completed_drains_non_consuming_until_acked() {
    let ledger = NamespaceExecutionLedger::new();
    let record = ok_record("namespace_execution_1", "workspace-session");

    ledger
        .record_completed(record.clone())
        .expect("record completed succeeds");

    // drain is a non-consuming peek: it stays pending until ack.
    assert_eq!(
        ledger
            .drain_completed_namespace_executions(10)
            .expect("drain succeeds"),
        vec![record.clone()]
    );
    assert_eq!(
        ledger
            .drain_completed_namespace_executions(10)
            .expect("drain succeeds"),
        vec![record.clone()]
    );

    ledger
        .ack_completed_namespace_executions(std::slice::from_ref(&record.namespace_execution_id))
        .expect("ack succeeds");
    assert!(ledger
        .drain_completed_namespace_executions(10)
        .expect("drain succeeds")
        .is_empty());
}

#[test]
fn retention_drop_records_partial_error() {
    let ledger = NamespaceExecutionLedger::with_limits(1, 4, 4);
    let first = ok_record("namespace_execution_1", "workspace-one");
    let second = ok_record("namespace_execution_2", "workspace-two");

    ledger
        .record_completed(first.clone())
        .expect("record first succeeds");
    ledger
        .record_completed(second.clone())
        .expect("record second succeeds");

    let pending = ledger
        .drain_completed_namespace_executions(10)
        .expect("drain succeeds");
    assert_eq!(pending.len(), 1);
    assert_eq!(
        pending[0].namespace_execution_id,
        second.namespace_execution_id
    );

    let errors = ledger.drain_partial_errors().expect("partial errors drain");
    assert!(
        errors.iter().any(|error| {
            error.contains(&first.namespace_execution_id.0) && error.contains("dropped")
        }),
        "{errors:?}"
    );
}
