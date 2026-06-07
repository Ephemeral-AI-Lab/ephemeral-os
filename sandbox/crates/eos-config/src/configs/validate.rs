//! Shared field-validation helpers and the common error type for config
//! sections. Each section's `validate()` expresses the semantic constraints
//! YAML deserialization cannot, returning [`ConfigFieldError`] on the first
//! violation.

use std::path::Path;

use thiserror::Error;

/// A config field that violated a semantic constraint.
#[derive(Debug, Clone, PartialEq, Eq, Error)]
#[error("{field}: {reason}")]
pub struct ConfigFieldError {
    pub field: &'static str,
    pub reason: String,
}

impl ConfigFieldError {
    /// Build an error for `field` with an explanatory `reason`.
    #[must_use]
    pub fn new(field: &'static str, reason: impl Into<String>) -> Self {
        Self {
            field,
            reason: reason.into(),
        }
    }
}

/// Require an absolute filesystem path.
pub fn require_absolute(path: &Path, field: &'static str) -> Result<(), ConfigFieldError> {
    if path.is_absolute() {
        Ok(())
    } else {
        Err(ConfigFieldError::new(field, "must be an absolute path"))
    }
}

/// Require a non-blank string.
pub fn require_non_empty(value: &str, field: &'static str) -> Result<(), ConfigFieldError> {
    if value.trim().is_empty() {
        Err(ConfigFieldError::new(field, "must be non-empty"))
    } else {
        Ok(())
    }
}

/// Require `UTC`, `Z`, or a fixed timestamp offset like `+08:00`.
pub fn require_timestamp_timezone(
    value: &str,
    field: &'static str,
) -> Result<(), ConfigFieldError> {
    let value = value.trim();
    if value.eq_ignore_ascii_case("UTC") || value == "Z" || parse_fixed_offset(value) {
        Ok(())
    } else {
        Err(ConfigFieldError::new(
            field,
            "must be UTC, Z, or a fixed offset like +08:00",
        ))
    }
}

/// Require that no list item is blank.
pub fn require_non_empty_items(
    values: &[String],
    field: &'static str,
) -> Result<(), ConfigFieldError> {
    if values.iter().any(|value| value.trim().is_empty()) {
        Err(ConfigFieldError::new(
            field,
            "must not contain empty strings",
        ))
    } else {
        Ok(())
    }
}

/// Require `value >= minimum`.
pub fn require_u32_at_least(
    value: u32,
    minimum: u32,
    field: &'static str,
) -> Result<(), ConfigFieldError> {
    if value >= minimum {
        Ok(())
    } else {
        Err(ConfigFieldError::new(
            field,
            format!("must be at least {minimum}"),
        ))
    }
}

/// Require `value >= minimum`.
pub fn require_u64_at_least(
    value: u64,
    minimum: u64,
    field: &'static str,
) -> Result<(), ConfigFieldError> {
    if value >= minimum {
        Ok(())
    } else {
        Err(ConfigFieldError::new(
            field,
            format!("must be at least {minimum}"),
        ))
    }
}

/// Require `value >= minimum`.
pub fn require_usize_at_least(
    value: usize,
    minimum: usize,
    field: &'static str,
) -> Result<(), ConfigFieldError> {
    if value >= minimum {
        Ok(())
    } else {
        Err(ConfigFieldError::new(
            field,
            format!("must be at least {minimum}"),
        ))
    }
}

/// Require a finite `value > minimum` (callers pass `0.0`).
pub fn require_f64_gt(
    value: f64,
    minimum: f64,
    field: &'static str,
) -> Result<(), ConfigFieldError> {
    if value.is_finite() && value > minimum {
        Ok(())
    } else {
        Err(ConfigFieldError::new(field, "must be greater than zero"))
    }
}

/// Require a finite `value >= minimum` (callers pass `0.0`).
pub fn require_f64_at_least(
    value: f64,
    minimum: f64,
    field: &'static str,
) -> Result<(), ConfigFieldError> {
    if value.is_finite() && value >= minimum {
        Ok(())
    } else {
        Err(ConfigFieldError::new(field, "must be at least zero"))
    }
}

/// Require a finite ratio in `(0.0, 1.0]`.
pub fn require_ratio(value: f64, field: &'static str) -> Result<(), ConfigFieldError> {
    if value.is_finite() && value > 0.0 && value <= 1.0 {
        Ok(())
    } else {
        Err(ConfigFieldError::new(
            field,
            "must be greater than 0.0 and at most 1.0",
        ))
    }
}

fn parse_fixed_offset(value: &str) -> bool {
    let bytes = value.as_bytes();
    if bytes.len() != 6 || !matches!(bytes[0], b'+' | b'-') || bytes[3] != b':' {
        return false;
    }
    let Ok(hour) = value[1..3].parse::<u8>() else {
        return false;
    };
    let Ok(minute) = value[4..6].parse::<u8>() else {
        return false;
    };
    hour <= 23 && minute <= 59
}
