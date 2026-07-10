use sandbox_operation_contract::{OperationRequest, OperationResponse, OperationScope};
use serde_json::{Map, Value};

use crate::error::{RequestDecodeError, BAD_JSON};

pub fn decode_request_value(value: Value) -> Result<OperationRequest, RequestDecodeError> {
    let Value::Object(object) = value else {
        return Err(RequestDecodeError::new(
            BAD_JSON,
            "request message must be a json object",
        ));
    };
    decode_request_object(object)
}

pub fn encode_request_line(request: &OperationRequest) -> Result<Vec<u8>, serde_json::Error> {
    crate::framing::encode_serializable_json_line(request)
}

pub fn encode_authenticated_request_line(
    request: &OperationRequest,
    auth_field: &str,
    auth_token: &str,
) -> Result<Vec<u8>, serde_json::Error> {
    let mut value = serde_json::to_value(request)?;
    if let Value::Object(object) = &mut value {
        object.insert(auth_field.to_owned(), Value::String(auth_token.to_owned()));
    }
    crate::framing::encode_json_line(&value)
}

pub fn decode_response_line(line: &[u8]) -> Result<OperationResponse, serde_json::Error> {
    serde_json::from_slice(line)
}

#[must_use]
pub fn response_line(response: &OperationResponse) -> Vec<u8> {
    crate::framing::encode_serializable_json_line(response).unwrap_or_default()
}

fn decode_request_object(
    mut object: Map<String, Value>,
) -> Result<OperationRequest, RequestDecodeError> {
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
    Ok(OperationRequest {
        op,
        request_id,
        scope,
        args,
    })
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
    RequestDecodeError::new(sandbox_operation_contract::error::INVALID_REQUEST, message)
}
