use std::sync::OnceLock;

use serde::{Deserialize, Deserializer, Serialize, Serializer};
use serde_json::value::RawValue;
use serde_json::{json, Value};

use crate::error::{OperationError, OPERATION_FAILED};

#[derive(Debug)]
pub struct OperationResponse {
    body: ResponseBody,
}

#[derive(Debug)]
enum ResponseBody {
    Value(Value),
    Raw {
        json: Box<RawValue>,
        parsed: OnceLock<Value>,
    },
}

impl OperationResponse {
    #[must_use]
    pub fn ok(result: Value) -> Self {
        Self {
            body: ResponseBody::Value(result),
        }
    }

    #[must_use]
    pub fn from_json_value(value: Value) -> Self {
        Self {
            body: ResponseBody::Value(value),
        }
    }

    /// Preserve one already-encoded JSON response without materializing a
    /// `serde_json::Value` tree. Deserialization remains value-backed, so raw
    /// responses are an internal producer-side memory optimization only.
    pub fn from_raw_json(json: String) -> Result<Self, serde_json::Error> {
        Ok(Self {
            body: ResponseBody::Raw {
                json: RawValue::from_string(json)?,
                parsed: OnceLock::new(),
            },
        })
    }

    #[must_use]
    pub fn running(result: Value) -> Self {
        Self::from_json_value(result)
    }

    #[must_use]
    pub fn service_error(error: impl std::fmt::Display) -> Self {
        Self::fault(OPERATION_FAILED, error.to_string())
    }

    #[must_use]
    pub fn unknown_op() -> Self {
        Self::fault("unknown_op", "unknown operation")
    }

    #[must_use]
    pub fn fault(kind: impl Into<String>, message: impl Into<String>) -> Self {
        Self::fault_with_details(kind, message, json!({}))
    }

    #[must_use]
    pub fn fault_with_details(
        kind: impl Into<String>,
        message: impl Into<String>,
        details: Value,
    ) -> Self {
        Self {
            body: ResponseBody::Value(
                OperationError::new(kind, message, details).into_json_value(),
            ),
        }
    }

    #[must_use]
    pub fn into_json_value(self) -> Value {
        match self.body {
            ResponseBody::Value(value) => value,
            ResponseBody::Raw { json, parsed } => parsed.into_inner().unwrap_or_else(|| {
                serde_json::from_str(json.get()).expect("RawValue has already validated the JSON")
            }),
        }
    }

    #[must_use]
    pub fn as_json_value(&self) -> &Value {
        match &self.body {
            ResponseBody::Value(value) => value,
            ResponseBody::Raw { json, parsed } => parsed.get_or_init(|| {
                serde_json::from_str(json.get()).expect("RawValue has already validated the JSON")
            }),
        }
    }
}

impl Clone for OperationResponse {
    fn clone(&self) -> Self {
        match &self.body {
            ResponseBody::Value(value) => Self::from_json_value(value.clone()),
            ResponseBody::Raw { json, .. } => Self::from_raw_json(json.get().to_owned())
                .expect("RawValue has already validated the JSON"),
        }
    }
}

impl PartialEq for OperationResponse {
    fn eq(&self, other: &Self) -> bool {
        self.as_json_value() == other.as_json_value()
    }
}

impl Serialize for OperationResponse {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        match &self.body {
            ResponseBody::Value(value) => value.serialize(serializer),
            ResponseBody::Raw { json, .. } => json.serialize(serializer),
        }
    }
}

impl<'de> Deserialize<'de> for OperationResponse {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        Value::deserialize(deserializer).map(Self::from_json_value)
    }
}

impl From<OperationResponse> for Value {
    fn from(response: OperationResponse) -> Self {
        response.into_json_value()
    }
}
