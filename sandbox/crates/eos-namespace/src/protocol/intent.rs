//! Shared verb intent for the wire protocol.
//!
//! [`Intent`] is the single verb-classification enum (serialized as its
//! snake_case `.value`).

use serde::{Deserialize, Serialize};

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
