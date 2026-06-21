use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

use crate::error_kind;
use crate::response::Response;
use crate::scope::OperationScope;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Request {
    pub op: String,
    pub request_id: String,
    pub scope: OperationScope,
    pub args: Value,
}

impl Request {
    #[must_use]
    pub fn new(
        op: impl Into<String>,
        request_id: impl Into<String>,
        scope: OperationScope,
        args: Value,
    ) -> Self {
        Self {
            op: op.into(),
            request_id: request_id.into(),
            scope,
            args,
        }
    }

    pub fn required_string(&self, field: &str) -> Result<String, Response> {
        self.field(field).and_then(|value| match value.as_str() {
            Some(value) if !value.is_empty() => Ok(value.to_owned()),
            Some(_) => Err(self.invalid_argument(format!("{field} must be non-empty"))),
            None => Err(self.invalid_argument(format!("{field} must be a string"))),
        })
    }

    pub fn optional_string(&self, field: &str) -> Result<Option<String>, Response> {
        match self.optional_field(field)? {
            Some(value) => match value.as_str() {
                Some(value) => Ok(Some(value.to_owned())),
                None => Err(self.invalid_argument(format!("{field} must be a string"))),
            },
            None => Ok(None),
        }
    }

    pub fn required_path(&self, field: &str) -> Result<std::path::PathBuf, Response> {
        Ok(std::path::PathBuf::from(self.required_string(field)?))
    }

    pub fn optional_path(&self, field: &str) -> Result<Option<std::path::PathBuf>, Response> {
        Ok(self.optional_string(field)?.map(std::path::PathBuf::from))
    }

    pub fn optional_u64(&self, field: &str) -> Result<Option<u64>, Response> {
        match self.optional_field(field)? {
            Some(value) => value
                .as_u64()
                .map(Some)
                .ok_or_else(|| self.invalid_argument(format!("{field} must be an integer"))),
            None => Ok(None),
        }
    }

    pub fn required_u64(&self, field: &str) -> Result<u64, Response> {
        self.field(field).and_then(|value| {
            value
                .as_u64()
                .ok_or_else(|| self.invalid_argument(format!("{field} must be an integer")))
        })
    }

    pub fn optional_usize(&self, field: &str) -> Result<Option<usize>, Response> {
        self.optional_u64(field)?
            .map(|value| {
                usize::try_from(value)
                    .map_err(|_| self.invalid_argument(format!("{field} is too large")))
            })
            .transpose()
    }

    pub fn required_usize(&self, field: &str) -> Result<usize, Response> {
        usize::try_from(self.required_u64(field)?)
            .map_err(|_| self.invalid_argument(format!("{field} is too large")))
    }

    pub fn optional_f64(&self, field: &str) -> Result<Option<f64>, Response> {
        match self.optional_field(field)? {
            Some(value) => match value.as_f64() {
                Some(value) if value.is_finite() => Ok(Some(value)),
                Some(_) => Err(self.invalid_argument(format!("{field} must be finite"))),
                None => Err(self.invalid_argument(format!("{field} must be a number"))),
            },
            None => Ok(None),
        }
    }

    fn field(&self, field: &str) -> Result<&Value, Response> {
        self.args_object()?
            .get(field)
            .ok_or_else(|| self.invalid_argument(format!("{field} is required for {}", self.op)))
    }

    fn optional_field(&self, field: &str) -> Result<Option<&Value>, Response> {
        Ok(self.args_object()?.get(field))
    }

    fn args_object(&self) -> Result<&Map<String, Value>, Response> {
        self.args
            .as_object()
            .ok_or_else(|| self.invalid_argument("args must be an object"))
    }

    pub fn invalid_argument(&self, message: impl Into<String>) -> Response {
        Response::fault(error_kind::INVALID_REQUEST, message)
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RequestDecodeError {
    kind: &'static str,
    message: String,
}

impl RequestDecodeError {
    #[must_use]
    pub const fn kind(&self) -> &'static str {
        self.kind
    }

    #[must_use]
    pub fn message(&self) -> &str {
        &self.message
    }
}

pub fn decode_request_object(
    mut object: Map<String, Value>,
) -> Result<Request, RequestDecodeError> {
    let op = remove_request_string(&mut object, "op")?;
    let request_id = remove_request_string(&mut object, "request_id")?;
    let scope = match object.remove("scope") {
        Some(scope) => serde_json::from_value::<OperationScope>(scope)
            .map_err(|error| invalid_request(format!("scope is invalid: {error}")))?,
        None => return Err(invalid_request("scope is required")),
    };
    scope.validate().map_err(invalid_request)?;
    let args = object
        .remove("args")
        .ok_or_else(|| invalid_request("request must include op, request_id, and args"))?;
    if op.trim().is_empty() {
        return Err(invalid_request("op is required"));
    }
    if !args.is_object() {
        return Err(invalid_request("args must be an object"));
    }
    Ok(Request {
        op,
        request_id,
        scope,
        args,
    })
}

pub fn decode_request_value(value: Value) -> Result<Request, RequestDecodeError> {
    let Value::Object(object) = value else {
        return Err(request_decode_error(
            error_kind::BAD_JSON,
            "request message must be a json object",
        ));
    };
    decode_request_object(object)
}

fn remove_request_string(
    object: &mut Map<String, Value>,
    field: &str,
) -> Result<String, RequestDecodeError> {
    let Some(Value::String(value)) = object.remove(field) else {
        return Err(invalid_request(format!(
            "{field} is required and must be a string"
        )));
    };
    Ok(value)
}

fn invalid_request(message: impl Into<String>) -> RequestDecodeError {
    request_decode_error(error_kind::INVALID_REQUEST, message)
}

fn request_decode_error(kind: &'static str, message: impl Into<String>) -> RequestDecodeError {
    RequestDecodeError {
        kind,
        message: message.into(),
    }
}
