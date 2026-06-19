//! Shared daemon error-envelope shaping.

use protocol::{FaultDetails, OperationEnvelope, OperationFault, ResponseMeta};
use serde_json::Value;

use crate::wire::ErrorKind;

pub(crate) fn to_wire_value(output: impl serde::Serialize) -> Value {
    serde_json::to_value(output).expect("operation output DTO serializes to JSON")
}

pub(crate) fn error_envelope(kind: ErrorKind, message: impl Into<String>, details: Value) -> Value {
    let fault = if kind == ErrorKind::InternalError {
        OperationFault::internal(message, fault_details(details))
    } else {
        OperationFault::new(kind.as_str(), message).with_details(fault_details(details))
    };
    to_wire_value(OperationEnvelope::<Value>::error(
        fault,
        ResponseMeta::default(),
    ))
}

fn fault_details(details: Value) -> FaultDetails {
    match details {
        Value::Null => FaultDetails::default(),
        Value::Object(fields) if fields.is_empty() => FaultDetails::default(),
        Value::Object(fields) => fields
            .into_iter()
            .fold(FaultDetails::default(), |details, (key, value)| {
                details.with_field(key, value)
            }),
        value => FaultDetails::default().with_field("value", value),
    }
}
