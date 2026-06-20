use serde_json::{json, Value};

#[derive(Debug, Clone)]
pub struct Response {
    value: Value,
}

impl Response {
    #[must_use]
    pub fn ok(result: Value) -> Self {
        Self { value: result }
    }

    #[must_use]
    pub fn running(result: Value) -> Self {
        Self { value: result }
    }

    #[must_use]
    pub fn service_error(error: impl std::fmt::Display) -> Self {
        Self::fault("operation_failed", error.to_string())
    }

    #[must_use]
    pub fn unknown_op() -> Self {
        Self::fault("unknown_op", "unknown operation")
    }

    #[must_use]
    pub fn fault(kind: &'static str, message: impl Into<String>) -> Self {
        Self {
            value: json!({
                "error": {
                    "kind": kind,
                    "message": message.into(),
                    "details": {},
                },
            }),
        }
    }

    #[must_use]
    pub fn into_json_value(self) -> Value {
        self.value
    }
}

impl From<Response> for Value {
    fn from(response: Response) -> Self {
        response.into_json_value()
    }
}

#[must_use]
pub fn error_response_with_details(
    kind: &str,
    message: impl Into<String>,
    details: Value,
) -> Value {
    json!({
        "error": {
            "kind": kind,
            "message": message.into(),
            "details": details,
        }
    })
}

#[must_use]
pub fn response_line(response: &Value) -> Vec<u8> {
    crate::framing::encode_json_line(response)
}
