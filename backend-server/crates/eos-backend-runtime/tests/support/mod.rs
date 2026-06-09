//! Shared backend-runtime test helpers.
#![allow(clippy::unwrap_used)]

use eos_backend_store::BackendStore;
use eos_types::RequestId;

/// A temp-backed [`BackendStore`]; keep the returned `TempDir` alive for the test.
pub async fn temp_store() -> (BackendStore, tempfile::TempDir) {
    let tmp = tempfile::tempdir().unwrap();
    let store = BackendStore::open(tmp.path().join("backend.db"))
        .await
        .unwrap();
    (store, tmp)
}

/// Parse a request id from a test literal.
pub fn rid(s: &str) -> RequestId {
    s.parse().unwrap()
}
