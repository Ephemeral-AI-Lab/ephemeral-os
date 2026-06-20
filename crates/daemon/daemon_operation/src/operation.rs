use std::path::PathBuf;

use serde_json::{json, Map, Value};

use crate::internal::services::DaemonOperations;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OperationFamily {
    Command,
    File,
    Workspace,
    Health,
    Run,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ArgKind {
    String,
    Integer,
    Float,
    Path,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ArgCliSpec {
    pub flag: Option<&'static str>,
    pub positional: Option<&'static str>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ArgSpec {
    pub name: &'static str,
    pub kind: ArgKind,
    pub required: bool,
    pub help: &'static str,
    pub default: Option<&'static str>,
    pub cli: Option<ArgCliSpec>,
}

impl ArgSpec {
    #[must_use]
    pub const fn required(
        name: &'static str,
        kind: ArgKind,
        help: &'static str,
        cli: Option<ArgCliSpec>,
    ) -> Self {
        Self {
            name,
            kind,
            required: true,
            help,
            default: None,
            cli,
        }
    }

    #[must_use]
    pub const fn optional(
        name: &'static str,
        kind: ArgKind,
        help: &'static str,
        default: Option<&'static str>,
        cli: Option<ArgCliSpec>,
    ) -> Self {
        Self {
            name,
            kind,
            required: false,
            help,
            default,
            cli,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CliSpec {
    pub path: &'static [&'static str],
    pub usage: &'static str,
    pub examples: &'static [&'static str],
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct OperationSpec {
    pub name: &'static str,
    pub family: OperationFamily,
    pub summary: &'static str,
    pub args: &'static [ArgSpec],
    pub cli: Option<CliSpec>,
}

pub type OperationDispatch = fn(&DaemonOperations, OperationRequest<'_>) -> OperationResponse;

#[derive(Clone, Copy)]
pub struct OperationEntry {
    pub spec: &'static OperationSpec,
    pub dispatch: OperationDispatch,
}

impl OperationEntry {
    #[must_use]
    pub const fn new(spec: &'static OperationSpec, dispatch: OperationDispatch) -> Self {
        Self { spec, dispatch }
    }
}

#[derive(Debug, Clone, Copy)]
pub struct OperationRequest<'a> {
    pub name: &'a str,
    pub request_id: &'a str,
    pub args: &'a Value,
}

impl<'a> OperationRequest<'a> {
    #[must_use]
    pub const fn new(name: &'a str, request_id: &'a str, args: &'a Value) -> Self {
        Self {
            name,
            request_id,
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

    pub fn required_path(&self, field: &str) -> Result<PathBuf, OperationResponse> {
        Ok(PathBuf::from(self.required_string(field)?))
    }

    pub fn optional_path(&self, field: &str) -> Result<Option<PathBuf>, OperationResponse> {
        Ok(self.optional_string(field)?.map(PathBuf::from))
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

    fn field(&self, field: &str) -> Result<&Value, OperationResponse> {
        self.args_object()?
            .get(field)
            .ok_or_else(|| self.invalid_argument(format!("{field} is required for {}", self.name)))
    }

    fn optional_field(&self, field: &str) -> Result<Option<&Value>, OperationResponse> {
        Ok(self.args_object()?.get(field))
    }

    fn args_object(&self) -> Result<&Map<String, Value>, OperationResponse> {
        self.args
            .as_object()
            .ok_or_else(|| self.invalid_argument("args must be an object"))
    }

    pub fn invalid_argument(&self, message: impl Into<String>) -> OperationResponse {
        OperationResponse::fault("invalid_request", message)
    }
}

#[derive(Debug, Clone)]
pub struct OperationResponse {
    value: Value,
}

impl OperationResponse {
    #[must_use]
    pub fn ok(_request: &OperationRequest<'_>, result: Value) -> Self {
        Self { value: result }
    }

    #[must_use]
    pub fn running(_request: &OperationRequest<'_>, result: Value) -> Self {
        Self { value: result }
    }

    #[must_use]
    pub fn service_error(_request: &OperationRequest<'_>, error: impl std::fmt::Display) -> Self {
        Self::fault("operation_failed", error.to_string())
    }

    #[must_use]
    pub fn unknown_op(request: &OperationRequest<'_>) -> Self {
        Self::fault("unknown_op", format!("unknown op: {}", request.name))
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

impl From<OperationResponse> for Value {
    fn from(response: OperationResponse) -> Self {
        response.into_json_value()
    }
}
