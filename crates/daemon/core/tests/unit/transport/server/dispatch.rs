use serde_json::json;

#[test]
fn protocol_version_accepts_supported_version() {
    assert!(protocol_version_error(Some(&json!(crate::wire::DAEMON_PROTOCOL_VERSION))).is_none());
}

#[test]
fn protocol_version_rejects_absent_version() {
    let response = protocol_version_error(None).expect("error response");

    assert_eq!(response["status"], "error");
    assert_eq!(response["error"]["kind"], "invalid_request");
    assert_eq!(
        response["error"]["details"]["fields"]["expected"],
        json!(crate::wire::DAEMON_PROTOCOL_VERSION)
    );
    assert_eq!(response["error"]["details"]["fields"]["found"], json!(null));
}

#[test]
fn protocol_version_rejects_unsupported_version() {
    let response = protocol_version_error(Some(&json!(999))).expect("error response");

    assert_eq!(response["status"], "error");
    assert_eq!(response["error"]["kind"], "invalid_request");
    assert_eq!(
        response["error"]["details"]["fields"]["expected"],
        json!(crate::wire::DAEMON_PROTOCOL_VERSION)
    );
    assert_eq!(response["error"]["details"]["fields"]["found"], json!(999));
}

#[test]
fn protocol_version_rejects_non_integer_version() {
    let response = protocol_version_error(Some(&json!("1"))).expect("error response");

    assert_eq!(response["status"], "error");
    assert_eq!(response["error"]["details"]["fields"]["found"], json!("1"));
}
