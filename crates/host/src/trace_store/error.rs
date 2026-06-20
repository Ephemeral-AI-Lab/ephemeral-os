use std::path::PathBuf;

#[derive(Debug, thiserror::Error)]
pub enum TraceStoreError {
    #[error("open trace store at {path}: {source}")]
    Open {
        path: PathBuf,
        #[source]
        source: rusqlite::Error,
    },
    #[error("trace store schema version {found} is newer than supported {supported}")]
    NewerSchema { found: u32, supported: u32 },
    #[error("trace store sqlite error: {0}")]
    Sqlite(#[from] rusqlite::Error),
    #[error("trace protobuf decode error: {0}")]
    ProstDecode(#[from] prost::DecodeError),
    #[error("trace store request-start append intentionally failed for test")]
    InjectedRequestStartFailure,
    #[error("trace store response-persisted append intentionally failed for test")]
    InjectedResponsePersistedFailure,
    #[error("trace event append intentionally failed for test")]
    InjectedTraceEventFailure,
}
