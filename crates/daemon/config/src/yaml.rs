//! YAML parser adapter for sandbox config documents.
//!
//! Keep the concrete parser here so config callers interact with
//! `ConfigDocument` and owner-crate schemas instead of parser-specific types.

pub(crate) use serde_yaml_ng::{from_str, to_string, Error, Mapping, Value};
