#![forbid(unsafe_code)]

use serde_json::json;
use trace::{BoundedJson, DetailBudget};

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
