//! Shared verb intent + file-size caps for the wire protocol.
//!
//! [`Intent`] is the single verb-classification enum (serialized as its
//! snake_case `.value`); the byte caps bound `read_file`/`write_file`/`edit_file`
//! payloads. The typed request/response DTOs that used to live here were
//! superseded by the daemon decoding raw `Value` into `eos-workspace-contract`
//! and `eos-workspace-runtime` types, and were removed as dead duplicates.

use serde::{Deserialize, Serialize};

/// Default `read_file` cap (over it raises `ValueError`). The daemon may
/// override this via `daemon.files.max_read_bytes`; this constant is the
/// fallback used when no runtime config is threaded.
pub const MAX_READ_BYTES: usize = 16 * 1024 * 1024;
/// Default per-file `write_file` / `edit_file` cap. The daemon may override this
/// via `daemon.files.max_write_bytes`; this constant is the fallback used when
/// no runtime config is threaded. Kept below the 16 MiB request frame so a
/// single file payload fits one envelope.
pub const MAX_FILE_BYTES: usize = 8 * 1024 * 1024;

/// The single enum in the verb model; serialized as its `.value` string.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Intent {
    /// `"read_only"`
    ReadOnly,
    /// `"write_allowed"`
    WriteAllowed,
    /// `"lifecycle"`
    Lifecycle,
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Value;

    type TestResult<T = ()> = Result<T, Box<dyn std::error::Error + Send + Sync>>;

    #[test]
    fn intent_wire_values() -> TestResult {
        assert_eq!(
            serde_json::to_value(Intent::ReadOnly)?,
            Value::String("read_only".to_owned())
        );
        assert_eq!(
            serde_json::to_value(Intent::WriteAllowed)?,
            Value::String("write_allowed".to_owned())
        );
        assert_eq!(
            serde_json::to_value(Intent::Lifecycle)?,
            Value::String("lifecycle".to_owned())
        );
        Ok(())
    }
}
