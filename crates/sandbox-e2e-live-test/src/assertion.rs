use serde_json::Value;

/// Assert there is no top-level `error` key (the success discriminator).
pub fn ok(resp: &Value) {
    assert!(
        resp.get("error").is_none(),
        "expected a success response, got error: {resp}"
    );
}

/// JSON-pointer get-or-panic. `field(resp, "/status")`, `field(resp, "/id")`, etc.
#[must_use]
pub fn field<'a>(resp: &'a Value, ptr: &str) -> &'a Value {
    resp.pointer(ptr)
        .unwrap_or_else(|| panic!("missing field {ptr} in response: {resp}"))
}
