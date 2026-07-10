use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

use crate::error::INVALID_REQUEST;
use crate::{OperationResponse, OperationScope};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct OperationRequest {
    pub op: String,
    pub request_id: String,
    pub scope: OperationScope,
    pub args: Value,
}

impl OperationRequest {
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

    pub fn required_string(&self, field: &str) -> Result<String, OperationResponse> {
        self.field(field).and_then(|value| match value.as_str() {
            Some(value) if !value.is_empty() => Ok(value.to_owned()),
            Some(_) => Err(self.invalid_argument(format!("{field} must be non-empty"))),
            None => Err(self.invalid_argument(format!("{field} must be a string"))),
        })
    }

    pub fn optional_string(&self, field: &str) -> Result<Option<String>, OperationResponse> {
        match self.optional_field(field)? {
            Some(value) => match value.as_str() {
                Some(value) => Ok(Some(value.to_owned())),
                None => Err(self.invalid_argument(format!("{field} must be a string"))),
            },
            None => Ok(None),
        }
    }

    pub fn required_path(&self, field: &str) -> Result<std::path::PathBuf, OperationResponse> {
        Ok(std::path::PathBuf::from(self.required_string(field)?))
    }

    pub fn optional_path(
        &self,
        field: &str,
    ) -> Result<Option<std::path::PathBuf>, OperationResponse> {
        Ok(self.optional_string(field)?.map(std::path::PathBuf::from))
    }

    pub fn optional_u64(&self, field: &str) -> Result<Option<u64>, OperationResponse> {
        match self.optional_field(field)? {
            Some(value) => value
                .as_u64()
                .map(Some)
                .ok_or_else(|| self.invalid_argument(format!("{field} must be an integer"))),
            None => Ok(None),
        }
    }

    pub fn required_u64(&self, field: &str) -> Result<u64, OperationResponse> {
        self.field(field).and_then(|value| {
            value
                .as_u64()
                .ok_or_else(|| self.invalid_argument(format!("{field} must be an integer")))
        })
    }

    pub fn optional_usize(&self, field: &str) -> Result<Option<usize>, OperationResponse> {
        self.optional_u64(field)?
            .map(|value| {
                usize::try_from(value)
                    .map_err(|_| self.invalid_argument(format!("{field} is too large")))
            })
            .transpose()
    }

    pub fn required_usize(&self, field: &str) -> Result<usize, OperationResponse> {
        usize::try_from(self.required_u64(field)?)
            .map_err(|_| self.invalid_argument(format!("{field} is too large")))
    }

    pub fn optional_f64(&self, field: &str) -> Result<Option<f64>, OperationResponse> {
        match self.optional_field(field)? {
            Some(value) => match value.as_f64() {
                Some(value) if value.is_finite() => Ok(Some(value)),
                Some(_) => Err(self.invalid_argument(format!("{field} must be finite"))),
                None => Err(self.invalid_argument(format!("{field} must be a number"))),
            },
            None => Ok(None),
        }
    }

    pub fn invalid_argument(&self, message: impl Into<String>) -> OperationResponse {
        OperationResponse::fault(INVALID_REQUEST, message)
    }

    fn field(&self, field: &str) -> Result<&Value, OperationResponse> {
        self.args_object()?
            .get(field)
            .ok_or_else(|| self.invalid_argument(format!("{field} is required for {}", self.op)))
    }

    fn optional_field(&self, field: &str) -> Result<Option<&Value>, OperationResponse> {
        Ok(self.args_object()?.get(field))
    }

    fn args_object(&self) -> Result<&Map<String, Value>, OperationResponse> {
        self.args
            .as_object()
            .ok_or_else(|| self.invalid_argument("args must be an object"))
    }
}
