use std::path::PathBuf;

use anyhow::{bail, Context, Result};
use serde_json::Value;

use super::DEFAULT_WORKSPACE_ROOT;

pub(crate) fn required_string_arg<'a>(args: &'a Value, name: &str) -> Result<&'a str> {
    match args.get(name) {
        Some(Value::String(value)) if !value.trim().is_empty() => Ok(value),
        Some(_) => bail!("{name} must be a non-empty string"),
        None => bail!("{name} is required"),
    }
}

pub(crate) fn optional_string_arg<'a>(args: &'a Value, name: &str) -> Option<&'a str> {
    match args.get(name) {
        Some(Value::String(value)) if !value.trim().is_empty() => Some(value),
        _ => None,
    }
}

pub(crate) fn workspace_root_from_args(args: &Value) -> Result<PathBuf> {
    let raw = optional_string_arg(args, "workspace_root").unwrap_or(DEFAULT_WORKSPACE_ROOT);
    let path = PathBuf::from(raw);
    if !path.is_absolute() {
        bail!("workspace_root must be absolute: {raw}");
    }
    Ok(path)
}

pub(crate) fn optional_u16_arg(args: &Value, name: &str) -> Result<Option<u16>> {
    let Some(value) = args.get(name) else {
        return Ok(None);
    };
    let raw = value
        .as_u64()
        .with_context(|| format!("{name} must be an integer"))?;
    let value = u16::try_from(raw).with_context(|| format!("{name} is out of range"))?;
    Ok(Some(value))
}

pub(crate) fn validate_container_name(name: &str) -> Result<()> {
    if name.is_empty()
        || name.starts_with('-')
        || !name
            .chars()
            .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '-' | '_' | '.'))
    {
        bail!("container name must contain only ASCII letters, digits, '.', '_' or '-'");
    }
    Ok(())
}
