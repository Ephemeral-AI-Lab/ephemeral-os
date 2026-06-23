use std::collections::BTreeSet;
use std::error::Error;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use rusqlite::Connection;
use sandbox_observability::{
    ObservabilityPaths, ObservabilityStore, SandboxSnapshotRecord, SpanRecord, StoreError,
    TraceRecord,
};

type TestResult<T = ()> = Result<T, Box<dyn Error>>;

struct TestDir {
    path: PathBuf,
}

impl TestDir {
    fn new(name: &str) -> TestResult<Self> {
        let unique = SystemTime::now().duration_since(UNIX_EPOCH)?.as_nanos();
        let path = std::env::temp_dir().join(format!(
            "sandbox-observability-{name}-{}-{unique}",
            std::process::id()
        ));
        fs::create_dir_all(&path)?;
        Ok(Self { path })
    }

    fn path(&self) -> &Path {
        &self.path
    }
}

impl Drop for TestDir {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.path);
    }
}

#[test]
fn schema_initialization_is_idempotent() -> TestResult {
    let (dir, paths) = test_paths("schema-idempotent")?;

    let first_store = ObservabilityStore::open(&paths)?;
    drop(first_store);
    let second_store = ObservabilityStore::open(&paths)?;
    drop(second_store);

    let connection = Connection::open(paths.database_path())?;
    assert_eq!(table_names(&connection)?, allowed_tables());
    assert_eq!(index_names(&connection)?, allowed_indexes());
    assert_eq!(migration_count(&connection)?, 1);
    assert!(paths.database_path().exists());
    assert!(dir
        .path()
        .join("daemon-runtime")
        .join("observability")
        .exists());

    Ok(())
}

#[test]
fn schema_initialization_rejects_migration_checksum_drift() -> TestResult {
    let (_dir, paths) = test_paths("schema-checksum-drift")?;
    let store = ObservabilityStore::open(&paths)?;
    drop(store);

    let connection = Connection::open(paths.database_path())?;
    connection.execute(
        "UPDATE schema_migrations
             SET checksum = 'fnv1a64:0000000000000000'
             WHERE version = 1",
        [],
    )?;
    drop(connection);

    let error = match ObservabilityStore::open(&paths) {
        Ok(_) => return Err("schema initialization accepted a stale checksum".into()),
        Err(error) => error,
    };

    match error {
        StoreError::MigrationChecksumMismatch {
            version,
            expected,
            actual,
        } => {
            assert_eq!(version, 1);
            assert!(expected.starts_with("fnv1a64:"));
            assert_eq!(actual, "fnv1a64:0000000000000000");
        }
        other => return Err(format!("unexpected schema initialization error: {other}").into()),
    }

    Ok(())
}

#[test]
fn inserts_synthetic_trace_and_spans() -> TestResult {
    let (_dir, paths) = test_paths("trace-span-insert")?;
    let store = ObservabilityStore::open(&paths)?;

    store.insert_trace(
        &TraceRecord {
            trace_id: "trace-1".to_owned(),
            kind: "request".to_owned(),
            status: "ok".to_owned(),
            sandbox_id: "sandbox-1".to_owned(),
            operation: "exec_command".to_owned(),
            request_id: Some("request-1".to_owned()),
            started_at_unix_ms: 1_000,
            finished_at_unix_ms: Some(1_025),
            duration_ms: Some(25.0),
            error_kind: None,
            error_message: None,
        },
        &[
            SpanRecord {
                span_id: "span-1".to_owned(),
                trace_id: "trace-1".to_owned(),
                parent_span_id: None,
                method_name: "dispatch_operation".to_owned(),
                call_index: 0,
                status: "ok".to_owned(),
                started_at_unix_ms: 1_000,
                finished_at_unix_ms: Some(1_005),
                duration_ms: Some(5.0),
                error_kind: None,
                error_message: None,
            },
            SpanRecord {
                span_id: "span-2".to_owned(),
                trace_id: "trace-1".to_owned(),
                parent_span_id: Some("span-1".to_owned()),
                method_name: "CommandOperationService::exec_command".to_owned(),
                call_index: 1,
                status: "ok".to_owned(),
                started_at_unix_ms: 1_005,
                finished_at_unix_ms: Some(1_025),
                duration_ms: Some(20.0),
                error_kind: None,
                error_message: None,
            },
        ],
    )?;

    let connection = Connection::open(paths.database_path())?;
    assert_eq!(row_count(&connection, "traces")?, 1);
    assert_eq!(row_count(&connection, "spans")?, 2);

    let request_id: String = connection.query_row(
        "SELECT request_id FROM traces WHERE trace_id = 'trace-1'",
        [],
        |row| row.get(0),
    )?;
    assert_eq!(request_id, "request-1");

    let max_call_index: i64 =
        connection.query_row("SELECT MAX(call_index) FROM spans", [], |row| row.get(0))?;
    assert_eq!(max_call_index, 1);

    Ok(())
}

#[test]
fn upserts_synthetic_sandbox_snapshot() -> TestResult {
    let (_dir, paths) = test_paths("snapshot-upsert")?;
    let store = ObservabilityStore::open(&paths)?;

    store.upsert_sandbox_snapshot(&SandboxSnapshotRecord {
        sandbox_id: "sandbox-1".to_owned(),
        state: "starting".to_owned(),
        sampled_at_unix_ms: 1_000,
        error_message: Some("warming up".to_owned()),
    })?;
    store.upsert_sandbox_snapshot(&SandboxSnapshotRecord {
        sandbox_id: "sandbox-1".to_owned(),
        state: "ready".to_owned(),
        sampled_at_unix_ms: 2_000,
        error_message: None,
    })?;

    let connection = Connection::open(paths.database_path())?;
    assert_eq!(row_count(&connection, "sandbox_snapshots")?, 1);

    let snapshot: (String, i64, Option<String>) = connection.query_row(
        "SELECT state, sampled_at_unix_ms, error_message
             FROM sandbox_snapshots
             WHERE sandbox_id = 'sandbox-1'",
        [],
        |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
    )?;

    assert_eq!(snapshot, ("ready".to_owned(), 2_000, None));

    Ok(())
}

fn test_paths(name: &str) -> TestResult<(TestDir, ObservabilityPaths)> {
    let dir = TestDir::new(name)?;
    let socket_path = dir.path().join("daemon-runtime").join("runtime.sock");
    let paths = ObservabilityPaths::from_socket_path(socket_path)?;
    Ok((dir, paths))
}

fn allowed_tables() -> BTreeSet<String> {
    ["schema_migrations", "sandbox_snapshots", "spans", "traces"]
        .into_iter()
        .map(String::from)
        .collect()
}

fn allowed_indexes() -> BTreeSet<String> {
    [
        "idx_spans_trace_call_index",
        "idx_traces_request",
        "idx_traces_sandbox_started",
    ]
    .into_iter()
    .map(String::from)
    .collect()
}

fn table_names(connection: &Connection) -> rusqlite::Result<BTreeSet<String>> {
    let mut statement = connection.prepare(
        "SELECT name
             FROM sqlite_schema
             WHERE type = 'table'
               AND name NOT LIKE 'sqlite_%'
             ORDER BY name",
    )?;

    let rows = statement.query_map([], |row| row.get::<_, String>(0))?;
    rows.collect::<Result<_, _>>()
}

fn index_names(connection: &Connection) -> rusqlite::Result<BTreeSet<String>> {
    let mut statement = connection.prepare(
        "SELECT name
             FROM sqlite_schema
             WHERE type = 'index'
               AND name NOT LIKE 'sqlite_%'
             ORDER BY name",
    )?;

    let rows = statement.query_map([], |row| row.get::<_, String>(0))?;
    rows.collect::<Result<_, _>>()
}

fn migration_count(connection: &Connection) -> rusqlite::Result<i64> {
    connection.query_row("SELECT COUNT(*) FROM schema_migrations", [], |row| {
        row.get(0)
    })
}

fn row_count(connection: &Connection, table: &str) -> rusqlite::Result<i64> {
    let sql = format!("SELECT COUNT(*) FROM {table}");
    connection.query_row(&sql, [], |row| row.get(0))
}
