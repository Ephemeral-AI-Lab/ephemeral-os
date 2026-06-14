use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DetailBudget {
    RequestArgsSummary,
    SpanFields,
    EventDetails,
    ResponseSummary,
    HeartbeatDetails,
    SidecarRecord,
    Custom(usize),
}

impl DetailBudget {
    #[must_use]
    pub const fn bytes(self) -> usize {
        match self {
            Self::RequestArgsSummary => 4 * 1024,
            Self::SpanFields => 2 * 1024,
            Self::EventDetails => 1024,
            Self::ResponseSummary => 2 * 1024,
            Self::HeartbeatDetails => 4 * 1024,
            Self::SidecarRecord => 64 * 1024,
            Self::Custom(bytes) => bytes,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BoundedJson {
    pub value: Value,
    pub truncated: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub sha256: Option<String>,
    pub original_len: usize,
}

impl BoundedJson {
    #[must_use]
    pub fn capture(value: Value, budget: DetailBudget) -> Self {
        let value = redact_for_audit(value);
        let serialized = serde_json::to_vec(&value).expect("serde_json::Value serializes");
        if serialized.len() <= budget.bytes() {
            return Self {
                value,
                truncated: false,
                sha256: None,
                original_len: serialized.len(),
            };
        }

        Self {
            value: json!({
                "truncated": true,
                "sha256": sha256_hex(&serialized),
                "original_len": serialized.len(),
            }),
            truncated: true,
            sha256: Some(sha256_hex(&serialized)),
            original_len: serialized.len(),
        }
    }

    #[must_use]
    pub fn empty_object() -> Self {
        Self::capture(json!({}), DetailBudget::Custom(2))
    }

    #[must_use]
    pub fn encoded_value(&self) -> String {
        serde_json::to_string(&self.value).expect("serde_json::Value serializes")
    }
}

/// Recursively redact semantically sensitive audit fields before size bounding
/// or hashing. Byte budgets prevent oversized payloads; they are not a secret
/// handling policy.
#[must_use]
pub fn redact_for_audit(value: Value) -> Value {
    match value {
        Value::Object(object) => Value::Object(
            object
                .into_iter()
                .map(|(key, value)| {
                    if is_sensitive_key(&key) {
                        (key, json!("[redacted]"))
                    } else {
                        (key, redact_for_audit(value))
                    }
                })
                .collect(),
        ),
        Value::Array(values) => Value::Array(values.into_iter().map(redact_for_audit).collect()),
        other => other,
    }
}

fn is_sensitive_key(key: &str) -> bool {
    let normalized = key.to_ascii_lowercase().replace('-', "_");
    matches!(
        normalized.as_str(),
        "auth" | "authorization" | "token" | "api_key" | "apikey" | "cookie"
    ) || normalized.ends_with("_token")
        || normalized.contains("password")
        || normalized.contains("secret")
        || normalized.contains("credential")
        || normalized.contains("private_key")
}

/// Lowercase hex SHA-256 digest of `bytes`. Shared by trace producers that
/// stamp content digests (detail-budget truncation, audit hashing).
pub fn sha256_hex(bytes: &[u8]) -> String {
    let digest = Sha256::digest(bytes);
    let mut output = String::with_capacity(digest.len() * 2);
    for byte in digest {
        use std::fmt::Write as _;
        write!(&mut output, "{byte:02x}").expect("writing to String cannot fail");
    }
    output
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn capture_redacts_sensitive_fields_before_budgeting() {
        let captured = BoundedJson::capture(
            json!({
                "cmd": "echo ok",
                "api_key": "sk-live",
                "nested": {
                    "password": "pw",
                    "auth_token_present": true,
                    "refresh_token": "refresh",
                },
                "headers": [
                    {"Authorization": "Bearer token"},
                    {"name": "safe"}
                ]
            }),
            DetailBudget::Custom(10_000),
        );

        assert_eq!(captured.value["cmd"], json!("echo ok"));
        assert_eq!(captured.value["api_key"], json!("[redacted]"));
        assert_eq!(captured.value["nested"]["password"], json!("[redacted]"));
        assert_eq!(
            captured.value["nested"]["refresh_token"],
            json!("[redacted]")
        );
        assert_eq!(captured.value["nested"]["auth_token_present"], json!(true));
        assert_eq!(
            captured.value["headers"][0]["Authorization"],
            json!("[redacted]")
        );
    }
}
