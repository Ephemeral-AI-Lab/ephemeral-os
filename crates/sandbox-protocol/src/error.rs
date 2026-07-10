pub const BAD_JSON: &str = "bad_json";
pub const REQUEST_TOO_LARGE: &str = "request_too_large";
pub const UNAUTHORIZED: &str = "unauthorized";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RequestDecodeError {
    kind: &'static str,
    message: String,
}

impl RequestDecodeError {
    #[must_use]
    pub(crate) fn new(kind: &'static str, message: impl Into<String>) -> Self {
        Self {
            kind,
            message: message.into(),
        }
    }

    #[must_use]
    pub const fn kind(&self) -> &'static str {
        self.kind
    }

    #[must_use]
    pub fn message(&self) -> &str {
        &self.message
    }
}
